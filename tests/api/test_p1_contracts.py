"""Focused P1 read/SSE contract checks (SPEC.md §8)."""

import uuid
from datetime import datetime, timezone

import pytest
from app.db import Provenance, Source, SourceParagraph
from app.main import app
from app.routes.sources import get_source
from app.schemas import (
    CitationSpanEvent,
    CitationsExtractedEvent,
    FindingCreatedEvent,
    FindingResponse,
    ProvenanceResponse,
    SourceResponse,
)


def test_p1_read_endpoints_are_exposed_in_openapi() -> None:
    paths = app.openapi()["paths"]

    assert "/api/jobs/{job_id}/citations" in paths
    assert "/api/sources/{source_id}" in paths


def test_p1_sse_payloads_include_stable_ids_and_original_spans() -> None:
    citation_id = uuid.uuid4()
    finding_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    extracted = CitationsExtractedEvent(
        count=1,
        citations=[CitationSpanEvent(id=citation_id, page=7, start=1022, end=1180)],
    )
    finding = FindingCreatedEvent(
        finding_id=finding_id,
        citation_id=citation_id,
        check="proposition",
        verdict="SUPPORTS",
        confidence=0.82,
        created_at=now,
    )

    assert extracted.model_dump(mode="json")["citations"] == [
        {"id": str(citation_id), "page": 7, "start": 1022, "end": 1180}
    ]
    assert finding.model_dump(mode="json")["finding_id"] == str(finding_id)

    response = FindingResponse(
        id=finding_id,
        check="proposition",
        verdict="SUPPORTS",
        confidence=0.82,
        rationale="The cited passage directly addresses the stated point.",
        created_at=now,
    )
    assert response.rationale == "The cited passage directly addresses the stated point."


def test_source_contract_returns_auditable_p3_provenance() -> None:
    retrieved_at = datetime.now(timezone.utc)
    source = SourceResponse(
        id=uuid.uuid4(),
        kind="opinion",
        case_name="Example v. Example",
        court=None,
        decision_date=None,
        docket_no=None,
        external_id="cl-123",
        text="Opinion text",
        provenance=ProvenanceResponse(
            id=uuid.uuid4(),
            url="https://www.courtlistener.com/api/rest/v4/clusters/123/",
            retrieved_at=retrieved_at,
            method="courtlistener",
            sha256="a" * 64,
            snapshot_ref=None,
            session_ref=None,
        ),
    )

    assert source.provenance is not None
    assert source.provenance.method == "courtlistener"
    assert source.provenance.sha256 == "a" * 64


class _SourceResult:
    def __init__(self, source: Source) -> None:
        self.source = source

    def scalar_one_or_none(self) -> Source:
        return self.source


class _SourceSession:
    def __init__(self, source: Source) -> None:
        self.source = source

    async def execute(self, _query) -> _SourceResult:
        return _SourceResult(self.source)


@pytest.mark.asyncio
async def test_source_endpoint_maps_stored_provenance_record() -> None:
    retrieved_at = datetime.now(timezone.utc)
    source = Source(id=uuid.uuid4(), kind="opinion", text="Opinion text")
    source.paragraphs = [
        SourceParagraph(id=uuid.uuid4(), source_id=source.id, para_no=1, opinion_part="majority", text="Text")
    ]
    source.provenance = Provenance(
        id=uuid.uuid4(),
        source_id=source.id,
        url="https://www.courtlistener.com/api/rest/v4/clusters/123/",
        retrieved_at=retrieved_at,
        method="courtlistener",
        sha256="b" * 64,
    )

    response = await get_source(source.id, _SourceSession(source))

    assert response.provenance is not None
    assert response.provenance.url == source.provenance.url
    assert response.provenance.sha256 == "b" * 64
