"""P4 signal read contracts remain additive to P1 citation responses."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest

from app.db import Citation, Signal
from app.routes.jobs import _to_citation_response, get_job_citations
from app.schemas import CitationsResponse
from worker.db import Signal as WorkerSignal


def test_signal_models_have_the_same_p4_columns() -> None:
    expected = {
        "id",
        "job_id",
        "citation_id",
        "section_ref",
        "provider",
        "kind",
        "score",
        "raw",
        "created_at",
    }
    assert expected <= set(Signal.__table__.columns.keys())
    assert expected <= set(WorkerSignal.__table__.columns.keys())


def test_citation_response_serializes_signals_without_raw_provider_data() -> None:
    job_id = uuid.uuid4()
    citation = Citation(
        id=uuid.uuid4(),
        job_id=job_id,
        raw_text="Example v. Example, 1 F.4th 1 (2024)",
        normalized="example v. example, 1 f.4th 1 (2024)",
        kind="full",
    )
    citation.claims = []
    citation.findings = []
    citation.signals = [
        Signal(
            job_id=job_id,
            citation_id=citation.id,
            provider="gptzero",
            kind="hallucination",
            score=0.4,
            raw={"secret_provider_detail": "server only"},
            created_at=datetime.now(timezone.utc),
        )
    ]

    response = _to_citation_response(citation)
    dumped = response.model_dump(mode="json")

    assert dumped["signals"] == [
        {
            "provider": "gptzero",
            "kind": "hallucination",
            "score": 0.4,
            "section_ref": None,
            "created_at": dumped["signals"][0]["created_at"],
        }
    ]
    assert "raw" not in dumped["signals"][0]


def test_signal_fields_default_to_empty_for_pre_p4_jobs() -> None:
    response = CitationsResponse(job_id=uuid.uuid4())

    assert response.citations == []
    assert response.page_signals == []


class _CitationsResult:
    def __init__(self, citations) -> None:
        self.citations = citations

    def scalars(self):
        return self

    def unique(self):
        return self

    def all(self):
        return self.citations


class _CitationsSession:
    def __init__(self, citation, page_signal) -> None:
        self.citation = citation
        self.page_signal = page_signal

    async def get(self, _model, _id):
        return object()

    async def execute(self, _query):
        return _CitationsResult([self.citation])

    async def scalars(self, _query):
        return [self.page_signal]


@pytest.mark.asyncio
async def test_citations_endpoint_returns_citation_and_page_signals() -> None:
    job_id = uuid.uuid4()
    citation = Citation(
        id=uuid.uuid4(),
        job_id=job_id,
        raw_text="Example v. Example, 1 F.4th 1 (2024)",
        normalized="example v. example, 1 f.4th 1 (2024)",
        kind="full",
    )
    citation.claims = []
    citation.findings = []
    citation.signals = []
    page_signal = Signal(
        job_id=job_id,
        section_ref="page:1",
        provider="gptzero",
        kind="ai_likelihood",
        score=0.2,
        raw={"documents": [{"completely_generated_prob": 0.2}]},
        created_at=datetime.now(timezone.utc),
    )

    response = await get_job_citations(job_id, _CitationsSession(citation, page_signal))

    assert response.citations[0].signals == []
    assert response.page_signals[0].section_ref == "page:1"
    assert response.page_signals[0].score == 0.2
