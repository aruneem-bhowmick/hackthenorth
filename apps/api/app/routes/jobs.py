import json
import uuid
from pathlib import Path

import sentry_sdk
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import Job, JobMode, JobStatus, default_expiry, get_session
from app.queue import get_arq_pool
from app.schemas import JobCreateResponse, JobStatusResponse
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

    job = Job(
        id=job_id,
        mode=mode,
        status=JobStatus.QUEUED.value,
        filename=file.filename or "upload.pdf",
        expires_at=default_expiry(),
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

    return JobCreateResponse(job_id=job_id)


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> JobStatusResponse:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, detail={"error": {"code": "NOT_FOUND", "message": "job not found"}})
    return _to_status_response(job)


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


def _to_status_response(job: Job) -> JobStatusResponse:
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        mode=job.mode,
        filename=job.filename,
        page_count=job.page_count,
        created_at=job.created_at,
        completed_at=job.completed_at,
        expires_at=job.expires_at,
        summary={},
    )


def _sse_frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
