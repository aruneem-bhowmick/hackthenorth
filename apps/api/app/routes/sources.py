"""Read contract for P1 CourtListener-backed source material."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import Source, get_session
from app.schemas import SourceParagraphResponse, SourceResponse


router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.get("/{source_id}", response_model=SourceResponse)
async def get_source(
    source_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> SourceResponse:
    """Return the stored source text and deterministic paragraphs for P1 review.

    P3 will augment this shape with a complete provenance record.  Until then
    the ``provenance`` response field is explicitly null rather than a partial
    record that could be mistaken for independently auditable evidence.
    """
    result = await session.execute(
        select(Source)
        .where(Source.id == source_id)
        .options(selectinload(Source.paragraphs))
    )
    source = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(
            404, detail={"error": {"code": "NOT_FOUND", "message": "source not found"}}
        )

    return SourceResponse(
        id=source.id,
        kind=source.kind,
        case_name=source.case_name,
        court=source.court,
        decision_date=source.decision_date,
        docket_no=source.docket_no,
        external_id=source.external_id,
        text=source.text,
        paragraphs=[
            SourceParagraphResponse(
                id=paragraph.id,
                opinion_part=paragraph.opinion_part,
                para_no=paragraph.para_no,
                page=paragraph.page,
                text=paragraph.text,
            )
            for paragraph in sorted(source.paragraphs, key=lambda item: item.para_no)
        ],
    )
