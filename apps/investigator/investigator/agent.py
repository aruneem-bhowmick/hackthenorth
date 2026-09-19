"""Bounded Browserbase/Stagehand investigation for unresolved citations.

This module owns no database or environment configuration.  The worker passes
credentials, domain policy, matching policy, and budget explicitly so the
agent remains testable and so verdict-adjacent matching thresholds are not
silently selected here.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from .domains import is_allowlisted, is_denylisted, may_fetch


class InvestigatorUnavailable(RuntimeError):
    """Raised when Browserbase/Stagehand cannot start an investigation."""


class InvestigationVerdict(StrEnum):
    """The investigator-owned subset of SPEC.md's existence vocabulary."""

    VERIFIED_OFFICIAL = "VERIFIED_OFFICIAL"
    WEAKLY_CORROBORATED = "WEAKLY_CORROBORATED"
    NOT_FOUND_ANYWHERE = "NOT_FOUND_ANYWHERE"
    UNVERIFIABLE = "UNVERIFIABLE"


@dataclass(frozen=True, slots=True)
class CitationContext:
    """Citation metadata used to compose searches and validate candidates."""

    case_name: str
    parties: tuple[str, ...] = ()
    court: str | None = None
    year: str | int | None = None
    docket_no: str | None = None
    # A worker-selected official discovery host. This only changes the search
    # query; fetch authorization remains exclusively allowlist/denylist based.
    official_search_domain: str | None = None
    # These candidates are introduced after the required search step and still
    # undergo normal allowlist, extraction, and match verification.
    direct_official_candidates: tuple[DirectOfficialCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class DirectOfficialCandidate:
    """A configured citation-to-URL candidate, not source evidence itself."""

    url: str
    resolver_name: str


@dataclass(frozen=True, slots=True)
class ExtractedCandidate:
    """The FR-INV-004 extraction payload, independent of persistence models."""

    case_name: str
    docket_no: str | None = None
    court: str | None = None
    decision_date: str | None = None
    opinion_text: str | None = None

    @property
    def year(self) -> str | None:
        """Best-effort year from the required decision-date field."""

        if not self.decision_date:
            return None
        return self.decision_date[:4]


@dataclass(frozen=True, slots=True)
class InvestigationBudget:
    """Per-run limits.  Callers load the SPEC defaults from runtime config."""

    max_searches: int = 3
    max_fetches: int = 5
    max_wall_time_seconds: float = 90.0

    def __post_init__(self) -> None:
        if (
            self.max_searches < 0
            or self.max_fetches < 0
            or self.max_wall_time_seconds < 0
        ):
            raise ValueError("investigation budget values must be non-negative")


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Untrusted search-result metadata; it is never treated as source text."""

    url: str
    title: str
    snippet: str | None = None
    discovery_method: str = "search"


@dataclass(frozen=True, slots=True)
class InvestigationOutcome:
    """A persistence-neutral description of a completed bounded run."""

    verdict: InvestigationVerdict
    live_view_url: str | None
    session_ref: str | None
    searches: int
    fetches: int
    official_match: ExtractedCandidate | None = None
    official_url: str | None = None
    discovery_method: str | None = None
    secondary_mentions: tuple[SearchResult, ...] = ()
    budget_exhausted: bool = False
    errors: tuple[str, ...] = ()


class InvestigatorSession(Protocol):
    """Minimal seam around the live SDK, used by deterministic unit tests."""

    live_view_url: str | None
    session_ref: str | None

    async def search(self, query: str) -> Sequence[SearchResult]: ...

    async def fetch_and_extract(self, url: str) -> ExtractedCandidate: ...

    async def close(self) -> None: ...


SessionFactory = Callable[[str], Awaitable[InvestigatorSession]]
CandidateMatcher = Callable[[ExtractedCandidate, CitationContext], bool]
SecondaryMentionMatcher = Callable[[SearchResult, CitationContext], bool]
LiveViewCallback = Callable[[str | None], Awaitable[None] | None]


class _CandidateExtraction(BaseModel):
    case_name: str
    docket_no: str | None = Field(...)
    court: str | None = Field(...)
    decision_date: str | None = Field(...)
    opinion_text: str | None = Field(...)


class _StagehandSession:
    """Production adapter using the Python SDK established by ADR-005."""

    def __init__(
        self,
        browser: object,
        stagehand: object,
        live_view_url: str | None,
        session_ref: str | None,
        browserbase_api_key: str,
    ) -> None:
        self._browser = browser
        self._stagehand = stagehand
        self.live_view_url = live_view_url
        self.session_ref = session_ref
        self._browserbase_api_key = browserbase_api_key

    @classmethod
    async def open(cls, browserbase_api_key: str) -> _StagehandSession:
        try:
            from browserbase import AsyncBrowserbase
            from stagehand import Stagehand, browserbase

            browser = await browserbase.launch(api_key=browserbase_api_key)
            stagehand = await Stagehand.create(browser=browser)
            live_view_url: str | None = None
            session_id = browser.session_id
            if session_id:
                # A live-view lookup must not make an otherwise usable
                # investigation fail.  The caller can still receive the final
                # result if Browserbase's debugger endpoint is transiently down.
                try:
                    async with AsyncBrowserbase(api_key=browserbase_api_key) as client:
                        live_urls = await client.sessions.debug(session_id)
                    live_view_url = live_urls.debugger_fullscreen_url
                except Exception:
                    live_view_url = None
            return cls(
                browser,
                stagehand,
                live_view_url,
                session_id,
                browserbase_api_key,
            )
        except Exception as error:
            # If Stagehand creation failed after browser launch, it owns no
            # usable agent; best-effort cleanup prevents an orphaned session.
            try:
                close = getattr(locals().get("browser", None), "close", None)
                if close is not None:
                    await close()
            except Exception:
                pass
            raise InvestigatorUnavailable(
                "unable to start Browserbase investigator"
            ) from error

    async def search(self, query: str) -> Sequence[SearchResult]:
        from stagehand import browserbase

        # Browserbase's typed search endpoint avoids spending most of P3's
        # 90-second wall clock on three model-driven search-page parses.  It
        # returns the same untrusted title/URL evidence; only
        # `fetch_and_extract` navigates to a candidate after `may_fetch`.
        response = await browserbase.search(
            api_key=self._browserbase_api_key,
            query=query,
            num_results=10,
        )
        return tuple(
            SearchResult(url=item.url, title=item.title, snippet=None)
            for item in response.results
        )

    async def fetch_and_extract(self, url: str) -> ExtractedCandidate:
        page = await self._stagehand.browser.context.new_page()
        await page.goto(url)
        extracted = await self._stagehand.extract(
            "Extract the decision's case name, docket number, court, decision date, and opinion text. "
            "Return null for a field that is not present; do not infer missing facts.",
            _CandidateExtraction,
            page=page,
        )
        data = extracted.data
        return ExtractedCandidate(
            case_name=data.case_name,
            docket_no=data.docket_no,
            court=data.court,
            decision_date=data.decision_date,
            opinion_text=data.opinion_text,
        )

    async def close(self) -> None:
        await self._stagehand.close()


def _query_variants(context: CitationContext) -> tuple[str, ...]:
    """Build up to three non-empty deterministic discovery queries."""

    primary = " ".join(
        value
        for value in (
            context.case_name,
            *context.parties,
            context.court or "",
            str(context.year) if context.year is not None else "",
            context.docket_no or "",
        )
        if value
    )
    official_query = (
        f"site:{context.official_search_domain} {context.case_name} "
        f"{context.parties[0] if context.parties else ''}"
        if context.official_search_domain
        else context.case_name
    )
    variants = (
        primary,
        " ".join(
            value
            for value in (
                context.case_name,
                context.court or "",
                str(context.year or ""),
            )
            if value
        ),
        official_query,
    )
    return tuple(dict.fromkeys(query for query in variants if query.strip()))


def _default_secondary_match(result: SearchResult, context: CitationContext) -> bool:
    """Conservative title/snippet check for non-official secondary mentions."""

    haystack = f"{result.title} {result.snippet or ''}".casefold()
    case_name = context.case_name.casefold().strip()
    if not case_name or case_name not in haystack:
        return False
    corroborators = (
        context.court,
        str(context.year) if context.year is not None else None,
    )
    return any(value and value.casefold() in haystack for value in corroborators)


def _candidate_priority(
    result: SearchResult, context: CitationContext, query_index: int
) -> tuple[int, int]:
    """Prefer the most specific official result before consuming fetch budget.

    Search results are gathered across the broad-to-narrow query sequence. A
    final ``site:`` result must not be starved by five unrelated official pages
    returned by earlier queries. This is discovery ordering only: every URL
    still passes the same allow/deny decision immediately before navigation.
    """

    haystack = _normalise_discovery_text(f"{result.title} {result.url}")
    score = 0
    case_name = _normalise_discovery_text(context.case_name)
    if case_name and case_name in haystack:
        score += 100
    for party in context.parties:
        normalized_party = _normalise_discovery_text(party)
        if normalized_party and normalized_party in haystack:
            score += 200
            break
    if context.year is not None and str(context.year) in haystack:
        score += 20
    if context.official_search_domain:
        host = (urlparse(result.url).hostname or "").casefold()
        if host == context.official_search_domain.casefold():
            score += 50
    if any(direct.url == result.url for direct in context.direct_official_candidates):
        score += 1_000
    # Higher query indexes are the deliberately more-specific variants. Keep
    # stable result ordering within an otherwise equal priority.
    return (-score, -query_index)


def _normalise_discovery_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


async def _notify(callback: LiveViewCallback | None, url: str | None) -> None:
    if callback is None:
        return
    result = callback(url)
    if inspect.isawaitable(result):
        await result


def _within_budget(
    started_at: float, budget: InvestigationBudget, clock: Callable[[], float]
) -> bool:
    return clock() - started_at < budget.max_wall_time_seconds


async def run_investigation(
    browserbase_api_key: str,
    citation_context: CitationContext,
    budget: InvestigationBudget,
    *,
    allowlist: Iterable[str],
    denylist: Iterable[str],
    match_candidate: CandidateMatcher,
    session_factory: SessionFactory | None = None,
    secondary_mention_matcher: SecondaryMentionMatcher | None = None,
    on_live_view: LiveViewCallback | None = None,
    clock: Callable[[], float] = monotonic,
) -> InvestigationOutcome:
    """Search, fetch, extract, and match under the supplied hard limits.

    ``match_candidate`` is deliberately injected.  FR-INV-005 requires a
    configurable name-similarity threshold, and this module must not choose or
    persist one before the product decision is approved.
    """

    if not browserbase_api_key.strip():
        raise InvestigatorUnavailable("Browserbase API key is required")

    started_at = clock()
    if budget.max_wall_time_seconds == 0 or budget.max_searches == 0:
        return InvestigationOutcome(
            verdict=InvestigationVerdict.NOT_FOUND_ANYWHERE,
            live_view_url=None,
            session_ref=None,
            searches=0,
            fetches=0,
            budget_exhausted=True,
        )

    factory = session_factory or _StagehandSession.open
    try:
        session = await factory(browserbase_api_key)
    except InvestigatorUnavailable:
        raise
    except Exception as error:
        raise InvestigatorUnavailable(
            "unable to start Browserbase investigator"
        ) from error
    searches = 0
    fetches = 0
    errors: list[str] = []
    secondary_mentions: list[SearchResult] = []
    candidates: list[tuple[int, SearchResult]] = []
    seen_urls: set[str] = set()
    exhausted = False
    secondary_match = secondary_mention_matcher or _default_secondary_match

    try:
        await _notify(on_live_view, session.live_view_url)

        for query_index, query in enumerate(_query_variants(citation_context)):
            if searches >= budget.max_searches:
                break
            if not _within_budget(started_at, budget, clock):
                exhausted = True
                break
            searches += 1
            try:
                remaining = budget.max_wall_time_seconds - (clock() - started_at)
                results = await asyncio.wait_for(
                    session.search(query), timeout=max(0.1, remaining)
                )
            except TimeoutError:
                exhausted = True
                errors.append("search timed out")
                break
            except Exception as error:
                errors.append(f"search failed: {error}")
                continue
            for result in results:
                if result.url in seen_urls:
                    continue
                seen_urls.add(result.url)
                # A denylisted result is neither evidence nor a fetch target.
                if is_denylisted(result.url, denylist):
                    continue
                if is_allowlisted(result.url, allowlist):
                    candidates.append((query_index, result))
                else:
                    try:
                        if secondary_match(result, citation_context):
                            secondary_mentions.append(result)
                    except Exception as error:
                        errors.append(
                            f"secondary mention matching failed for {result.url}: {error}"
                        )

        for direct in citation_context.direct_official_candidates:
            if direct.url in seen_urls:
                continue
            seen_urls.add(direct.url)
            if is_denylisted(direct.url, denylist):
                continue
            if is_allowlisted(direct.url, allowlist):
                candidates.append(
                    (
                        len(_query_variants(citation_context)),
                        SearchResult(
                            url=direct.url,
                            title=citation_context.case_name,
                            discovery_method=(
                                f"official_citation_resolver:{direct.resolver_name}"
                            ),
                        ),
                    )
                )

        for _, result in sorted(
            candidates,
            key=lambda item: _candidate_priority(item[1], citation_context, item[0]),
        ):
            if fetches >= budget.max_fetches:
                exhausted = True
                break
            if not _within_budget(started_at, budget, clock):
                exhausted = True
                break
            # Keep the security decision immediately adjacent to the method
            # that navigates to untrusted candidate content (INV-4).
            if not may_fetch(result.url, allowlist=allowlist, denylist=denylist):
                continue
            fetches += 1
            try:
                remaining = budget.max_wall_time_seconds - (clock() - started_at)
                candidate = await asyncio.wait_for(
                    session.fetch_and_extract(result.url), timeout=max(0.1, remaining)
                )
            except TimeoutError:
                exhausted = True
                errors.append(f"fetch/extract timed out for {result.url}")
                break
            except Exception as error:
                errors.append(f"fetch/extract failed for {result.url}: {error}")
                continue
            try:
                accepted = match_candidate(candidate, citation_context)
            except Exception as error:
                errors.append(f"candidate matching failed for {result.url}: {error}")
                continue
            if accepted:
                return InvestigationOutcome(
                    verdict=InvestigationVerdict.VERIFIED_OFFICIAL,
                    live_view_url=session.live_view_url,
                    session_ref=session.session_ref,
                    searches=searches,
                    fetches=fetches,
                    official_match=candidate,
                    official_url=result.url,
                    discovery_method=result.discovery_method,
                    secondary_mentions=tuple(secondary_mentions),
                    budget_exhausted=exhausted,
                    errors=tuple(errors),
                )

        if not exhausted and not _within_budget(started_at, budget, clock):
            exhausted = True
        verdict = (
            InvestigationVerdict.WEAKLY_CORROBORATED
            if secondary_mentions
            else InvestigationVerdict.UNVERIFIABLE
            if errors
            else InvestigationVerdict.NOT_FOUND_ANYWHERE
        )
        return InvestigationOutcome(
            verdict=verdict,
            live_view_url=session.live_view_url,
            session_ref=session.session_ref,
            searches=searches,
            fetches=fetches,
            secondary_mentions=tuple(secondary_mentions),
            budget_exhausted=exhausted,
            errors=tuple(errors),
        )
    finally:
        try:
            remaining = max(0.1, budget.max_wall_time_seconds - (clock() - started_at))
            await asyncio.wait_for(session.close(), timeout=min(5.0, remaining))
        except Exception:
            # Closing a completed run is best effort; do not replace its
            # evidence outcome with a cleanup failure.
            pass


__all__ = [
    "CandidateMatcher",
    "CitationContext",
    "DirectOfficialCandidate",
    "ExtractedCandidate",
    "InvestigationBudget",
    "InvestigationOutcome",
    "InvestigationVerdict",
    "InvestigatorSession",
    "InvestigatorUnavailable",
    "SearchResult",
    "run_investigation",
]
