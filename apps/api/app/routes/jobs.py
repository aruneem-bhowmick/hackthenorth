import hashlib
import hmac
import json
import secrets
import uuid
from pathlib import Path

import sentry_sdk
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.db import BriefPage, Citation, Finding, Job, JobMode, JobStatus, default_expiry, get_session
from app.queue import get_arq_pool
from app.schemas import (
    CitationResponse,
    CitationSpanResponse,
    CitationsResponse,
    ClaimResponse,
    FindingResponse,
    JobCreateResponse,
    JobStatusResponse,
    BriefPageResponse,
    BriefPagesResponse,
)
from app.sse import subscribe_events

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_ALLOWED_MODES = {m.value for m in JobMode}


@router.post("", response_model=JobCreateResponse)
async def create_job(
    file: UploadFile = File(...),
    mode: str = Form(...),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> JobCreateResponse:
    if mode not in _ALLOWED_MODES:
        raise HTTPException(
            400,
            detail={
                "error": {
                    "code": "UNSUPPORTED_FILE",
                    "message": f"mode must be one of {sorted(_ALLOWED_MODES)}",
                }
            },
        )
    # NFR-SEC-002 (true MIME sniffing, malformed-PDF handling) is P4 scope;
    # P0 checks the declared content type only.
    if file.content_type not in ("application/pdf", "application/x-pdf"):
        raise HTTPException(
            400,
            detail={"error": {"code": "UNSUPPORTED_FILE", "message": "only PDF uploads are accepted"}},
        )

    job_id = uuid.uuid4()
    uploads_dir = Path(settings.uploads_dir)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    dest = uploads_dir / f"{job_id}.pdf"

    size = 0
    with dest.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > settings.max_upload_bytes:
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    413,
                    detail={"error": {"code": "TOO_LARGE", "message": "file exceeds 25 MB"}},
                )
            out.write(chunk)

    review_token = secrets.token_urlsafe(32)
    job = Job(
        id=job_id,
        mode=mode,
        status=JobStatus.QUEUED.value,
        filename=file.filename or "upload.pdf",
        expires_at=default_expiry(),
        review_token_hash=_token_hash(review_token),
    )
    session.add(job)
    await session.commit()

    pool = await get_arq_pool()
    # NFR-REL-002: arq's _job_id set equal to our own job id makes re-enqueue idempotent.
    await pool.enqueue_job(
        "process_job",
        str(job_id),
        sentry_trace=sentry_sdk.get_traceparent(),
        sentry_baggage=sentry_sdk.get_baggage(),
        _job_id=str(job_id),
    )

    return JobCreateResponse(job_id=job_id, review_token=review_token)


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> JobStatusResponse:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, detail={"error": {"code": "NOT_FOUND", "message": "job not found"}})
    return await _to_status_response(job, session)


@router.get("/{job_id}/citations", response_model=CitationsResponse)
async def get_job_citations(
    job_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> CitationsResponse:
    """Return extracted citations and every completed finding in brief order.

    This is intentionally read-only: P1 workers persist independent citation
    results, and reconnecting clients can recover their complete review state
    from this endpoint even if an SSE message was missed.
    """
    if await session.get(Job, job_id) is None:
        raise HTTPException(404, detail={"error": {"code": "NOT_FOUND", "message": "job not found"}})

    result = await session.execute(
        select(Citation)
        .where(Citation.job_id == job_id)
        .options(
            selectinload(Citation.claims),
            selectinload(Citation.findings),
            selectinload(Citation.source_acquisition),
        )
        .order_by(Citation.page.nulls_last(), Citation.start_offset.nulls_last(), Citation.id)
    )
    citations = result.scalars().unique().all()
    return CitationsResponse(
        job_id=job_id,
        citations=[_to_citation_response(citation) for citation in citations],
    )


@router.get("/{job_id}/pages", response_model=BriefPagesResponse)
async def get_job_pages(
    job_id: uuid.UUID,
    x_pincite_review_token: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> BriefPagesResponse:
    """Return extracted brief text and citation spans for its upload session."""
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, detail={"error": {"code": "NOT_FOUND", "message": "job not found"}})
    if not job.review_token_hash or not x_pincite_review_token or not hmac.compare_digest(
        job.review_token_hash, _token_hash(x_pincite_review_token)
    ):
        raise HTTPException(403, detail={"error": {"code": "FORBIDDEN", "message": "review token required"}})
    pages = list(await session.scalars(select(BriefPage).where(BriefPage.job_id == job_id).order_by(BriefPage.page)))
    citations = list(await session.scalars(select(Citation).where(Citation.job_id == job_id)))
    spans: dict[int, list[CitationSpanResponse]] = {}
    for citation in citations:
        if citation.page is not None and citation.start_offset is not None and citation.end_offset is not None:
            spans.setdefault(citation.page, []).append(
                CitationSpanResponse(id=citation.id, start=citation.start_offset, end=citation.end_offset)
            )
    return BriefPagesResponse(
        job_id=job_id,
        pages=[BriefPageResponse(page=item.page, text=item.text, citations=spans.get(item.page, [])) for item in pages],
    )


@router.get("/{job_id}/events")
async def job_events(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> StreamingResponse:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, detail={"error": {"code": "NOT_FOUND", "message": "job not found"}})

    async def event_stream():
        yield _sse_frame("job.status", {"status": job.status})
        if job.status == JobStatus.COMPLETED.value:
            yield _sse_frame("job.completed", {"summary": {}})
            return
        if job.status == JobStatus.FAILED.value:
            yield _sse_frame("job.failed", {"error": "unknown"})
            return
        # P0 simplification: no event log, so Last-Event-ID replay (ADR-007)
        # only recovers "current status" on reconnect, not missed intermediate
        # events. Full replay needs persisted events — out of P0 scope.
        async for message in subscribe_events(str(job_id)):
            yield _sse_frame(message["event"], message["data"])
            if message["event"] in ("job.completed", "job.failed"):
                break

    return StreamingResponse(event_stream(), media_type="text/event-stream")


async def _to_status_response(job: Job, session: AsyncSession) -> JobStatusResponse:
    # A verdict count is a deliberately compact status summary.  The detailed
    # evidence remains on /citations, avoiding duplicated document text in the
    # polling response.
    try:
        result = await session.execute(
            select(Finding.verdict, func.count(Finding.id))
            .join(Citation, Finding.citation_id == Citation.id)
            .where(Citation.job_id == job.id)
            .group_by(Finding.verdict)
        )
    except ProgrammingError as exc:
        # During a rolling P0 -> P1 deploy, an API instance can briefly see a
        # database before Alembic has created the review tables.  The P0 job
        # itself is still useful; return its status without a P1 summary.
        if "relation \"findings\" does not exist" not in str(exc):
            raise
        summary = {}
    else:
        summary = {verdict: count for verdict, count in result.all()}
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        mode=job.mode,
        filename=job.filename,
        page_count=job.page_count,
        created_at=job.created_at,
        completed_at=job.completed_at,
        expires_at=job.expires_at,
        summary=summary,
    )


def _to_citation_response(citation: Citation) -> CitationResponse:
    return CitationResponse(
        id=citation.id,
        raw_text=citation.raw_text,
        normalized=citation.normalized,
        kind=citation.kind,
        antecedent_id=citation.antecedent_id,
        page=citation.page,
        start_offset=citation.start_offset,
        end_offset=citation.end_offset,
        pinpoint=citation.pinpoint,
        case_name=citation.case_name,
        court_hint=citation.court_hint,
        year_hint=citation.year_hint,
        resolution_state=citation.resolution_state,
        source_state=citation.source_acquisition.state if citation.source_acquisition else None,
        claims=[
            ClaimResponse(
                id=claim.id,
                proposition_text=claim.proposition_text,
                prop_start=claim.prop_start,
                prop_end=claim.prop_end,
                quote_text=claim.quote_text,
                quote_start=claim.quote_start,
                quote_end=claim.quote_end,
                quote_processing_start=claim.quote_processing_start,
                quote_processing_end=claim.quote_processing_end,
            )
            for claim in citation.claims
        ],
        findings=[
            FindingResponse(
                id=finding.id,
                check=finding.check,
                verdict=finding.verdict,
                confidence=finding.confidence,
                notes=finding.notes,
                evidence=finding.evidence,
                created_at=finding.created_at,
            )
            for finding in sorted(citation.findings, key=lambda item: (item.created_at, str(item.id)))
        ],
    )


def _sse_frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
