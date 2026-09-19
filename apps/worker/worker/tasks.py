import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sentry_sdk

from worker.config import get_settings
from worker.db import Job, JobStatus, get_sessionmaker
from worker.sse import publish_event


async def process_job(
    ctx: dict[str, Any],
    job_id: str,
    sentry_trace: str | None = None,
    sentry_baggage: str | None = None,
) -> None:
    """FR-SYS-001/002/003 P0 "hello job": flip queued -> processing -> completed,
    publishing SSE events at each transition. Real ingestion/extraction
    (FR-ING-*, P1) has not happened yet — this only confirms the file the
    API wrote to the shared upload volume actually arrived.
    """
    headers = {}
    if sentry_trace:
        headers["sentry-trace"] = sentry_trace
    if sentry_baggage:
        headers["baggage"] = sentry_baggage

    # continue_trace() returns a Transaction pre-configured with the trace_id
    # / parent_span_id parsed from `headers` — it is not a context manager
    # to wrap a separate start_transaction() call (that starts a second,
    # disconnected transaction with its own fresh trace_id instead).
    transaction = sentry_sdk.continue_trace(headers, op="queue.task", name="process_job")
    with sentry_sdk.start_transaction(transaction) as txn:
        txn.set_tag("job_id", job_id)
        await _process_job(job_id)


async def _process_job(job_id: str) -> None:
    sessionmaker = get_sessionmaker()
    settings = get_settings()

    async with sessionmaker() as session:
        job = await session.get(Job, uuid.UUID(job_id))
        if job is None:
            return

        try:
            job.status = JobStatus.PROCESSING.value
            await session.commit()
            await publish_event(job_id, "job.status", {"status": job.status})

            pdf_path = Path(settings.uploads_dir) / f"{job_id}.pdf"
            if not pdf_path.exists() or pdf_path.stat().st_size == 0:
                raise FileNotFoundError(f"uploaded file missing or empty: {pdf_path}")

            job.status = JobStatus.COMPLETED.value
            job.completed_at = datetime.now(timezone.utc)
            await session.commit()
            await publish_event(job_id, "job.completed", {"summary": {}})
        except Exception as exc:  # noqa: BLE001 — degrade to failed, never crash the worker
            await session.rollback()
            job.status = JobStatus.FAILED.value
            await session.commit()
            await publish_event(job_id, "job.failed", {"error": str(exc)})
            raise
