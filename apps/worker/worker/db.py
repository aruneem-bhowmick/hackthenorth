"""Worker-side mirror of the API's Postgres models.

P1 has multiple shared records, so the former P0 ``jobs``-only mirror is no
longer sufficient.  Keep column/table shapes in sync with ``apps/api/app/db``;
the worker uses these classes for pipeline persistence while Alembic remains
owned by the API service.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from worker.config import get_settings


class Base(DeclarativeBase):
    pass


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
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
    resolution_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_acquisition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_acquisitions.id", ondelete="SET NULL"), nullable=True, index=True
    )

    job: Mapped[Job] = relationship(back_populates="citations")
    claims: Mapped[list["Claim"]] = relationship(
        back_populates="citation", cascade="all, delete-orphan", passive_deletes=True
    )
    findings: Mapped[list["Finding"]] = relationship(
        back_populates="citation", cascade="all, delete-orphan", passive_deletes=True
    )
    investigator_runs: Mapped[list["InvestigatorRun"]] = relationship(
        back_populates="citation", cascade="all, delete-orphan", passive_deletes=True
    )
    source_acquisition: Mapped["SourceAcquisition | None"] = relationship(back_populates="citations")


class Claim(Base):
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
    provenance: Mapped["Provenance | None"] = relationship(
        back_populates="source", uselist=False, cascade="all, delete-orphan", passive_deletes=True
    )


class SourceParagraph(Base):
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


class SourceAcquisition(Base):
    __tablename__ = "source_acquisitions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    cluster_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), nullable=True
    )
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    citations: Mapped[list[Citation]] = relationship(back_populates="source_acquisition")


class Provenance(Base):
    """Auditable retrieval metadata for one stored source (FR-PRV-001)."""

    __tablename__ = "provenance"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    session_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)

    source: Mapped[Source] = relationship(back_populates="provenance")


class InvestigatorRun(Base):
    """Durable state for the bounded fallback investigation of one citation."""

    __tablename__ = "investigator_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    citation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("citations.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    searches: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fetches: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    live_view_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    outcome: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    citation: Mapped[Citation] = relationship(back_populates="investigator_runs")


class LookupCache(Base):
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
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    citation: Mapped[Citation] = relationship(back_populates="findings")


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        # Each citation task owns a session while it resolves a source. Match
        # the pool to WorkerSettings.max_jobs so a burst cannot exhaust the
        # database connection budget and become queued retries.
        _engine = create_async_engine(
            get_settings().database_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=0,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker
