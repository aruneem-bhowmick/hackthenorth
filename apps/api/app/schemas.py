import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class JobCreateResponse(BaseModel):
    job_id: uuid.UUID


class JobStatusResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    mode: str
    filename: str
    page_count: int | None
    created_at: datetime
    completed_at: datetime | None
    expires_at: datetime
    # P1: compact verdict counts; detailed evidence is returned by /citations.
    summary: dict[str, int] = Field(default_factory=dict)


class ClaimResponse(BaseModel):
    id: uuid.UUID
    proposition_text: str | None
    prop_start: int | None
    prop_end: int | None
    quote_text: str | None
    quote_start: int | None
    quote_end: int | None
    quote_processing_start: int | None
    quote_processing_end: int | None


class FindingResponse(BaseModel):
    id: uuid.UUID
    check: str
    verdict: str
    confidence: float | None
    notes: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CitationResponse(BaseModel):
    id: uuid.UUID
    raw_text: str
    normalized: str
    kind: str
    antecedent_id: uuid.UUID | None
    page: int | None
    start_offset: int | None
    end_offset: int | None
    pinpoint: str | None
    case_name: str | None
    court_hint: str | None
    year_hint: int | None
    resolution_state: str | None
    claims: list[ClaimResponse] = Field(default_factory=list)
    findings: list[FindingResponse] = Field(default_factory=list)


class CitationsResponse(BaseModel):
    job_id: uuid.UUID
    citations: list[CitationResponse] = Field(default_factory=list)


class SourceParagraphResponse(BaseModel):
    id: uuid.UUID
    opinion_part: str | None
    para_no: int
    page: int | None
    text: str


class SourceResponse(BaseModel):
    id: uuid.UUID
    kind: str
    case_name: str | None
    court: str | None
    decision_date: datetime | None
    docket_no: str | None
    external_id: str | None
    text: str | None
    paragraphs: list[SourceParagraphResponse] = Field(default_factory=list)
    # Full auditable provenance (URL, hash, snapshot and retrieval method) is
    # explicitly P3 scope.  Returning null is intentionally more honest than
    # manufacturing an incomplete provenance record in P1.
    provenance: None = None


# Typed P1 SSE payloads.  The worker publishes their JSON representation; the
# API's event endpoint remains transport-only so it can stream without buffering
# all citation work in the request process.
class CitationSpanEvent(BaseModel):
    id: uuid.UUID
    page: int | None
    start: int | None
    end: int | None


class CitationsExtractedEvent(BaseModel):
    count: int
    citations: list[CitationSpanEvent]


class FindingCreatedEvent(BaseModel):
    finding_id: uuid.UUID
    citation_id: uuid.UUID
    check: str
    verdict: str
    confidence: float | None
    created_at: datetime


class DependencyStatus(BaseModel):
    status: str  # "not_configured" | "verified" | "error"
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str  # "ok" (process is up) — does not imply all dependencies are verified
    dependencies: dict[str, DependencyStatus]
