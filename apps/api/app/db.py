"""Database models owned by the API.

P1 adds the durable records needed to make citation processing independently
retryable and reviewable (SPEC.md §§7--8).  This deliberately stops short of
the full ``provenance`` and investigator records: those are P3 work.  The
worker mirrors these models because both services access the same Postgres
tables; keep their table shapes in sync.
"""

import enum
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import get_settings


class Base(DeclarativeBase):
    pass


class JobMode(str, enum.Enum):
    BEFORE_FILING = "before_filing"
    ANSWERING_BRIEF = "answering_brief"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=JobStatus.QUEUED.value
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    # Populated in P1 (FR-ING-002 opens the PDF); P0 never parses it.
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    review_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    citations: Mapped[list["Citation"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )
    pages: Mapped[list["BriefPage"]] = relationship(back_populates="job", cascade="all, delete-orphan", passive_deletes=True)


class BriefPage(Base):
    __tablename__ = "brief_pages"
    __table_args__ = (UniqueConstraint("job_id", "page", name="uq_brief_pages_job_page"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    job: Mapped[Job] = relationship(back_populates="pages")


class Citation(Base):
    """An eyecite result and its original brief location (FR-EXT-001..004)."""

    __tablename__ = "citations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    antecedent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("citations.id", ondelete="SET NULL"), nullable=True
    )
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pinpoint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    case_name: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    court_hint: Mapped[str | None] = mapped_column(String(256), nullable=True)
    year_hint: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # FR-RES-002's status is kept separately from an existence finding so an
    # UNRECOGNIZED CourtListener response is not misrepresented as a verdict.
    resolution_state: Mapped[str | None] = mapped_column(String(32), nullable=True)

    job: Mapped[Job] = relationship(back_populates="citations")
    claims: Mapped[list["Claim"]] = relationship(
        back_populates="citation", cascade="all, delete-orphan", passive_deletes=True
    )
    findings: Mapped[list["Finding"]] = relationship(
        back_populates="citation", cascade="all, delete-orphan", passive_deletes=True
    )


class Claim(Base):
    """Citation-attached proposition/quotation spans (P1 currently writes quotes).

    ``quote_start``/``quote_end`` preserve the original brief offset required
    by SPEC.md §7.1.  The processing offsets retain the normalised-text span
    used by the P1 quote aligner, so normalisation never destroys reviewable
    source locations (FR-ING-002..003, FR-EXT-003).
    """

    __tablename__ = "claims"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    citation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("citations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    proposition_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    prop_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prop_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    quote_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote_processing_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote_processing_end: Mapped[int | None] = mapped_column(Integer, nullable=True)

    citation: Mapped[Citation] = relationship(back_populates="claims")


class Source(Base):
    """A CourtListener-resolved authority and its retrieved source text.

    P1 stores its content locally for quote checking.  P3 owns the auditable
    provenance record and investigator-specific retrieval metadata.
    """

    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    case_name: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    court: Mapped[str | None] = mapped_column(String(256), nullable=True)
    decision_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    docket_no: Mapped[str | None] = mapped_column(String(256), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(256), nullable=True, unique=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)

    paragraphs: Mapped[list["SourceParagraph"]] = relationship(
        back_populates="source", cascade="all, delete-orphan", passive_deletes=True
    )


class SourceParagraph(Base):
    """Deterministic local source chunks used by P1 quote matching/display."""

    __tablename__ = "source_paragraphs"
    __table_args__ = (UniqueConstraint("source_id", "para_no", name="uq_source_paragraphs_source_para"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    opinion_part: Mapped[str | None] = mapped_column(String(32), nullable=True)
    para_no: Mapped[int] = mapped_column(Integer, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    source: Mapped[Source] = relationship(back_populates="paragraphs")


class LookupCache(Base):
    """CourtListener lookup response cache keyed by normalised citation."""

    __tablename__ = "lookup_cache"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    normalized_citation: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    response: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), nullable=True
    )
    cached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Finding(Base):
    """One reproducible check result for one citation (SPEC.md §7.1)."""

    __tablename__ = "findings"
    __table_args__ = (UniqueConstraint("citation_id", "check", name="uq_findings_citation_check"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    citation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("citations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    check: Mapped[str] = mapped_column(String(32), nullable=False)
    verdict: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    citation: Mapped[Citation] = relationship(back_populates="findings")


def default_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=get_settings().job_expiry_hours)


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def get_session():
    async with get_sessionmaker()() as session:
        yield session
