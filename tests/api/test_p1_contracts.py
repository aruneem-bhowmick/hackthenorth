"""Focused P1 read/SSE contract checks (SPEC.md §8)."""

import uuid
from datetime import datetime, timezone

from app.main import app
from app.schemas import (
    CitationSpanEvent,
    CitationsExtractedEvent,
    FindingCreatedEvent,
    FindingResponse,
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


def test_source_contract_keeps_p3_provenance_explicitly_unavailable() -> None:
    source = SourceResponse(
        id=uuid.uuid4(),
        kind="opinion",
        case_name="Example v. Example",
        court=None,
        decision_date=None,
        docket_no=None,
        external_id="cl-123",
        text="Opinion text",
    )

    assert source.provenance is None
