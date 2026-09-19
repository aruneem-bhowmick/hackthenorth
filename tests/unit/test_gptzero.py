"""P4 GPTZero adapters stay best-effort and cannot write verdicts."""

from __future__ import annotations

import json
from types import SimpleNamespace
import uuid

import httpx
import pytest

from worker import tasks
from worker.gptzero import (
    GPTZeroScore,
    score_ai_likelihood,
    score_hallucination,
)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_ai_likelihood_uses_the_documented_probability() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://api.gptzero.me/v2/predict/text")
        assert request.headers["x-api-key"] == "test-key"
        assert json.loads(request.content) == {
            "document": "A sufficiently long page of brief text."
        }
        return httpx.Response(
            200, json={"documents": [{"completely_generated_prob": 0.73}]}
        )

    async with _client(handler) as client:
        score = await score_ai_likelihood(
            "test-key", "A sufficiently long page of brief text.", client=client
        )

    assert score == 0.73


@pytest.mark.asyncio
async def test_hallucination_adapter_abstains_when_public_response_has_no_score() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"documents": [{"completely_generated_prob": 0.73}]}
        )

    async with _client(handler) as client:
        score = await score_hallucination(
            "test-key", "A sufficiently long proposition sentence.", client=client
        )

    assert score is None


@pytest.mark.asyncio
async def test_hallucination_adapter_accepts_only_an_explicit_provider_score() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"documents": [{"hallucination_score": 0.41}]})

    async with _client(handler) as client:
        score = await score_hallucination(
            "test-key", "A sufficiently long proposition sentence.", client=client
        )

    assert score == 0.41


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    [
        lambda _request: (_ for _ in ()).throw(httpx.ReadTimeout("timeout")),
        lambda _request: httpx.Response(429, json={"detail": "quota exhausted"}),
    ],
)
async def test_provider_failures_abstain_without_raising(handler) -> None:
    async with _client(handler) as client:
        score = await score_ai_likelihood(
            "test-key", "A sufficiently long page of brief text.", client=client
        )

    assert score is None


class _SignalSession:
    def __init__(self) -> None:
        self.added = []
        self.committed = False

    async def scalar(self, _query):
        return None

    async def scalars(self, _query):
        return [SimpleNamespace(page=1, text="A sufficiently long page of brief text.")]

    def add(self, value) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None


class _SessionMaker:
    def __init__(self, session: _SignalSession) -> None:
        self.session = session

    def __call__(self) -> _SignalSession:
        return self.session


@pytest.mark.asyncio
async def test_inv2_signal_persistence_cannot_change_an_existing_finding(monkeypatch) -> None:
    """Required INV-2: signal availability leaves all finding fields identical."""

    session = _SignalSession()
    finding = SimpleNamespace(
        verdict="SUPPORTS",
        confidence=0.88,
        notes=["validated"],
        evidence={"paragraphs": ["p1"]},
    )
    before = (
        finding.verdict,
        finding.confidence,
        list(finding.notes),
        dict(finding.evidence),
    )
    monkeypatch.setattr(tasks, "get_settings", lambda: SimpleNamespace(gptzero_api_key="test-key"))
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: _SessionMaker(session))

    async def score(*_args, **_kwargs):
        return GPTZeroScore(score=0.4, raw={"documents": [{"hallucination_score": 0.4}]})

    monkeypatch.setattr(tasks, "score_hallucination_result", score)
    await tasks._score_proposition_signal_best_effort(
        {}, uuid.uuid4(), uuid.uuid4(), "A sufficiently long proposition sentence."
    )

    assert (
        finding.verdict,
        finding.confidence,
        finding.notes,
        finding.evidence,
    ) == before
    assert session.committed is True
    assert len(session.added) == 1
    assert session.added[0].kind == "hallucination"
    assert session.added[0].score == 0.4


@pytest.mark.asyncio
async def test_page_signal_provider_outage_leaves_review_records_untouched(monkeypatch) -> None:
    """A configured-but-unavailable GPTZero call cannot fail the review job."""

    session = _SignalSession()
    monkeypatch.setattr(tasks, "get_settings", lambda: SimpleNamespace(gptzero_api_key="test-key"))
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: _SessionMaker(session))

    async def unavailable(*_args, **_kwargs):
        return None

    monkeypatch.setattr(tasks, "score_ai_likelihood_result", unavailable)
    await tasks._score_brief_page_signals_best_effort({}, uuid.uuid4())

    assert session.added == []
    assert session.committed is True
