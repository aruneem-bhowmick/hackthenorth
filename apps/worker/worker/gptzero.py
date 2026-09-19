"""Best-effort GPTZero signal adapter (FR-SIG-001..002).

GPTZero's public text-prediction endpoint is documented at
``POST /v2/predict/text`` with an ``x-api-key`` header.  Its documented
``completely_generated_prob`` field is used only for P4's AI-writing signal.
The public docs do not currently specify a stable hallucination-score schema,
so that adapter accepts only an explicitly returned hallucination field and
otherwise abstains.  It never substitutes an AI-writing probability for a
hallucination signal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx


PREDICT_TEXT_URL = "https://api.gptzero.me/v2/predict/text"
_TIMEOUT = httpx.Timeout(connect=3.0, read=8.0, write=8.0, pool=3.0)
_LOGGER = logging.getLogger(__name__)


class GPTZeroUnavailable(RuntimeError):
    """The signal provider did not return a usable score."""


@dataclass(frozen=True)
class GPTZeroScore:
    """A validated provider score and the response retained for audit only."""

    score: float
    raw: dict[str, Any]


async def score_hallucination(
    api_key: str,
    text: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> float | None:
    """Return the provider's explicit hallucination score, or abstain safely."""

    scored = await score_hallucination_result(api_key, text, client=client)
    return scored.score if scored is not None else None


async def score_ai_likelihood(
    api_key: str,
    text: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> float | None:
    """Return GPTZero's documented AI-writing likelihood, or abstain safely."""

    scored = await score_ai_likelihood_result(api_key, text, client=client)
    return scored.score if scored is not None else None


async def score_hallucination_result(
    api_key: str,
    text: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> GPTZeroScore | None:
    """Return a detailed hallucination result for durable signal persistence."""

    try:
        payload = await _predict(api_key, text, client=client)
        document = _document(payload)
        value = _first_score(
            document,
            "hallucination_score",
            "hallucination_probability",
            "hallucination_prob",
        )
        if value is None:
            raise GPTZeroUnavailable("GPTZero response did not include a hallucination score")
        return GPTZeroScore(score=value, raw=payload)
    except GPTZeroUnavailable as exc:
        _LOGGER.warning("GPTZero hallucination signal unavailable: %s", exc)
        return None


async def score_ai_likelihood_result(
    api_key: str,
    text: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> GPTZeroScore | None:
    """Return a detailed AI-likelihood result for durable signal persistence."""

    try:
        payload = await _predict(api_key, text, client=client)
        value = _first_score(_document(payload), "completely_generated_prob")
        if value is None:
            raise GPTZeroUnavailable("GPTZero response did not include an AI-likelihood score")
        return GPTZeroScore(score=value, raw=payload)
    except GPTZeroUnavailable as exc:
        _LOGGER.warning("GPTZero AI-writing signal unavailable: %s", exc)
        return None


async def _predict(
    api_key: str,
    text: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    if not api_key:
        raise GPTZeroUnavailable("GPTZero is not configured")
    # The documented endpoint rejects very short strings. Empty brief pages
    # do not carry a meaningful section signal, so do not spend provider quota.
    if len(text.strip()) < 10:
        raise GPTZeroUnavailable("text is too short for a GPTZero signal")

    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        response = await active_client.post(
            PREDICT_TEXT_URL,
            headers={"x-api-key": api_key},
            json={"document": text},
        )
    except httpx.HTTPError as exc:
        raise GPTZeroUnavailable("GPTZero request failed") from exc
    finally:
        if owns_client:
            await active_client.aclose()
    if response.status_code != 200:
        raise GPTZeroUnavailable(f"GPTZero returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise GPTZeroUnavailable("GPTZero returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise GPTZeroUnavailable("GPTZero returned an unexpected payload")
    return payload


def _document(payload: dict[str, Any]) -> dict[str, Any]:
    documents = payload.get("documents")
    if isinstance(documents, list) and documents and isinstance(documents[0], dict):
        return documents[0]
    # Some documented examples expose document fields at the response root.
    return payload


def _first_score(document: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = document.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            score = float(value)
            if 0.0 <= score <= 1.0:
                return score
    return None
