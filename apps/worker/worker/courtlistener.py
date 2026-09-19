"""Typed CourtListener citation-lookup adapter for P1 resolution.

The adapter deliberately only implements CourtListener work.  It does not
pretend that a 404 is a fabricated authority (that investigation is P3), and
it leaves *id.* / *supra* resolution to the deterministic antecedent resolver
in the pipeline, as CourtListener's endpoint does not resolve those forms.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx


_LOOKUP_URL = "https://www.courtlistener.com/api/rest/v4/citation-lookup/"
MAX_CITATIONS_PER_REQUEST = 250
MAX_TEXT_CHARS_PER_REQUEST = 64_000


class CourtListenerUnavailable(RuntimeError):
    """The upstream could not give an answer after bounded retry attempts."""


@dataclass(frozen=True)
class CitationLookup:
    citation: str
    normalized_citations: tuple[str, ...]
    start_index: int
    end_index: int
    status: int
    error_message: str
    clusters: tuple[dict[str, Any], ...]

    @property
    def p1_verdict(self) -> str:
        """Return only the P1 provisional existence vocabulary.

        `UNRECOGNIZED` remains deliberately outside this mapping: SPEC.md has
        no approved P1 existence verdict for an upstream 400 response.
        Orchestration must keep it a neutral resolution state until that
        product decision is made.
        """

        if self.status == 200:
            return "VERIFIED"
        if self.status == 404:
            return "NOT_IN_DATABASE"
        if self.status == 300:
            return "AMBIGUOUS"
        raise ValueError(f"no approved P1 verdict for CourtListener status {self.status}")


RateLimitAcquire = Callable[[int], Awaitable[None]]


class CourtListenerClient:
    """Small async client with explicit request limits and retry behavior."""

    def __init__(
        self,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        acquire_rate_limit: RateLimitAcquire | None = None,
        max_retries: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._token = token
        self._client = client
        self._acquire_rate_limit = acquire_rate_limit
        self._max_retries = max_retries
        self._sleep = sleep

    async def lookup_text(self, text: str, *, expected_citations: int) -> list[CitationLookup]:
        """Look up one already-size-checked text batch.

        The caller owns grouping extracted full citations into batches.  This
        keeps short-form/antecedent mapping separate from the external API and
        makes the CourtListener hard limits explicit at the boundary.
        """

        if len(text) > MAX_TEXT_CHARS_PER_REQUEST:
            raise ValueError("CourtListener lookup text exceeds 64,000 characters")
        if expected_citations > MAX_CITATIONS_PER_REQUEST:
            raise ValueError("CourtListener lookup exceeds 250 citations")
        if expected_citations < 0:
            raise ValueError("expected_citations cannot be negative")

        if self._acquire_rate_limit:
            await self._acquire_rate_limit(expected_citations)

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(20.0))
        try:
            response = await self._post_with_retry(client, text)
            try:
                payload = response.json()
            except ValueError as exc:
                raise CourtListenerUnavailable("CourtListener returned invalid JSON") from exc
            if not isinstance(payload, list):
                raise CourtListenerUnavailable("CourtListener returned an unexpected payload")
            return [_parse_lookup(item) for item in payload]
        finally:
            if owns_client:
                await client.aclose()

    async def _post_with_retry(self, client: httpx.AsyncClient, text: str) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            try:
                response = await client.post(
                    _LOOKUP_URL,
                    headers={"Authorization": f"Token {self._token}"},
                    data={"text": text},
                )
            except httpx.HTTPError as exc:
                if attempt == self._max_retries:
                    raise CourtListenerUnavailable("CourtListener request failed") from exc
                await self._sleep(2**attempt)
                continue

            if response.status_code != 429:
                if response.is_error:
                    raise CourtListenerUnavailable(
                        f"CourtListener returned HTTP {response.status_code}"
                    )
                return response

            if attempt == self._max_retries:
                raise CourtListenerUnavailable("CourtListener remained rate limited")
            await self._sleep(2**attempt)

        raise AssertionError("unreachable")

    async def fetch_cluster_opinions(self, cluster: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch opinion records belonging to a citation-lookup cluster.

        CourtListener clusters may already include ``sub_opinions``. When they
        do not, use the cluster's API resource/id to retrieve that list first.
        Only CourtListener API URLs assembled from trusted identifiers are
        requested here; the brief never controls a fetch URL.
        """

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(20.0))
        try:
            opinion_urls = cluster.get("sub_opinions")
            if not isinstance(opinion_urls, list):
                cluster_id = cluster.get("id")
                if not cluster_id:
                    return []
                cluster_response = await self._get_json(
                    client, f"https://www.courtlistener.com/api/rest/v4/clusters/{int(cluster_id)}/"
                )
                opinion_urls = cluster_response.get("sub_opinions", [])
            opinions: list[dict[str, Any]] = []
            for url in opinion_urls:
                if not isinstance(url, str) or not url.startswith(
                    "https://www.courtlistener.com/api/rest/v4/opinions/"
                ):
                    continue
                opinions.append(await self._get_json(client, url))
            return opinions
        finally:
            if owns_client:
                await client.aclose()

    async def _get_json(self, client: httpx.AsyncClient, url: str) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            try:
                response = await client.get(url, headers={"Authorization": f"Token {self._token}"})
            except httpx.HTTPError as exc:
                if attempt == self._max_retries:
                    raise CourtListenerUnavailable("CourtListener opinion fetch failed") from exc
                await self._sleep(2**attempt)
                continue
            if response.status_code == 429:
                if attempt == self._max_retries:
                    raise CourtListenerUnavailable("CourtListener opinion fetch remained rate limited")
                await self._sleep(2**attempt)
                continue
            if response.is_error:
                raise CourtListenerUnavailable(f"CourtListener returned HTTP {response.status_code}")
            payload = response.json()
            if not isinstance(payload, dict):
                raise CourtListenerUnavailable("CourtListener opinion response was not an object")
            return payload
        raise AssertionError("unreachable")


def _parse_lookup(item: object) -> CitationLookup:
    if not isinstance(item, dict):
        raise CourtListenerUnavailable("CourtListener returned a non-object result")
    try:
        status = int(item["status"])
        citation = str(item["citation"])
        normalized = tuple(str(value) for value in item.get("normalized_citations", []))
        clusters = tuple(value for value in item.get("clusters", []) if isinstance(value, dict))
        return CitationLookup(
            citation=citation,
            normalized_citations=normalized,
            start_index=int(item.get("start_index", 0)),
            end_index=int(item.get("end_index", 0)),
            status=status,
            error_message=str(item.get("error_message", "")),
            clusters=clusters,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CourtListenerUnavailable("CourtListener result did not match its contract") from exc
