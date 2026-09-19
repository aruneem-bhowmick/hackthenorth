"""P4 export endpoint; reports are rendered from durable review records only."""

from __future__ import annotations

import uuid

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import Citation, Job, Source, get_session
from app.reporting import ExportLanguageError, lint_export_text, render_markdown, render_pdf


router = APIRouter(prefix="/api/jobs", tags=["exports"])


@router.get("/{job_id}/export")
async def export_job(
    job_id: uuid.UUID,
    report_format: str = Query(..., alias="format", pattern="^(fixlist|evidence)$"),
    export_type: str = Query("md", alias="type", pattern="^(md|pdf)$"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Return neutral Markdown or PDF evidence reports for one persisted job."""

    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, detail={"error": {"code": "NOT_FOUND", "message": "job not found"}})
    result = await session.execute(
        select(Citation)
        .where(Citation.job_id == job_id)
        .options(
            selectinload(Citation.claims),
            selectinload(Citation.findings),
            selectinload(Citation.source_acquisition),
        )
    )
    citations = result.scalars().unique().all()
    source_ids = {
        acquisition.source_id
        for citation in citations
        if (acquisition := citation.source_acquisition) is not None and acquisition.source_id is not None
    }
    sources_by_id: dict[str, Source] = {}
    if source_ids:
        source_rows = await session.execute(
            select(Source)
            .where(Source.id.in_(source_ids))
            .options(selectinload(Source.paragraphs), selectinload(Source.provenance))
        )
        sources_by_id = {str(source.id): source for source in source_rows.scalars().unique().all()}
    markdown = render_markdown(job, citations, sources_by_id, report_format)
    try:
        lint_export_text(markdown)
    except ExportLanguageError as exc:
        sentry_sdk.capture_exception(exc)
        raise HTTPException(
            500,
            detail={"error": {"code": "EXPORT_LANGUAGE_VIOLATION", "message": "report generation failed"}},
        ) from exc
    filename = f"pincite-{report_format}-{job_id}.{export_type}"
    if export_type == "pdf":
        return Response(
            render_pdf(markdown),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return Response(
        markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
