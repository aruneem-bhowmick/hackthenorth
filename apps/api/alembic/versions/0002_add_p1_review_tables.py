"""add P1 citation, source, cache, and finding tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-19

The full provenance and investigator tables remain P3 scope.  P1 source
content is retained only to support CourtListener-backed quote checking and
the source review endpoint (SPEC.md §§7--8).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "citations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("normalized", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("antecedent_id", sa.Uuid(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("start_offset", sa.Integer(), nullable=True),
        sa.Column("end_offset", sa.Integer(), nullable=True),
        sa.Column("pinpoint", sa.String(length=128), nullable=True),
        sa.Column("case_name", sa.String(length=1024), nullable=True),
        sa.Column("court_hint", sa.String(length=256), nullable=True),
        sa.Column("year_hint", sa.Integer(), nullable=True),
        sa.Column("resolution_state", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["antecedent_id"], ["citations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_citations_job_id", "citations", ["job_id"])
    op.create_index("ix_citations_normalized", "citations", ["normalized"])

    op.create_table(
        "claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("citation_id", sa.Uuid(), nullable=False),
        sa.Column("proposition_text", sa.Text(), nullable=True),
        sa.Column("prop_start", sa.Integer(), nullable=True),
        sa.Column("prop_end", sa.Integer(), nullable=True),
        sa.Column("quote_text", sa.Text(), nullable=True),
        sa.Column("quote_start", sa.Integer(), nullable=True),
        sa.Column("quote_end", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["citation_id"], ["citations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_claims_citation_id", "claims", ["citation_id"])

    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("case_name", sa.String(length=1024), nullable=True),
        sa.Column("court", sa.String(length=256), nullable=True),
        sa.Column("decision_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("docket_no", sa.String(length=256), nullable=True),
        sa.Column("external_id", sa.String(length=256), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_id"),
    )

    op.create_table(
        "source_paragraphs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("opinion_part", sa.String(length=32), nullable=True),
        sa.Column("para_no", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "para_no", name="uq_source_paragraphs_source_para"),
    )
    op.create_index("ix_source_paragraphs_source_id", "source_paragraphs", ["source_id"])

    op.create_table(
        "lookup_cache",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("normalized_citation", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("cached_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_citation"),
    )

    op.create_table(
        "findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("citation_id", sa.Uuid(), nullable=False),
        sa.Column("check", sa.String(length=32), nullable=False),
        sa.Column("verdict", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("notes", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["citation_id"], ["citations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # P1 emits at most one result for a given check on a citation.  This is
        # the database backstop for retry-safe citation tasks (NFR-REL-002).
        sa.UniqueConstraint("citation_id", "check", name="uq_findings_citation_check"),
    )
    op.create_index("ix_findings_citation_id", "findings", ["citation_id"])


def downgrade() -> None:
    op.drop_index("ix_findings_citation_id", table_name="findings")
    op.drop_table("findings")
    op.drop_table("lookup_cache")
    op.drop_index("ix_source_paragraphs_source_id", table_name="source_paragraphs")
    op.drop_table("source_paragraphs")
    op.drop_table("sources")
    op.drop_index("ix_claims_citation_id", table_name="claims")
    op.drop_table("claims")
    op.drop_index("ix_citations_normalized", table_name="citations")
    op.drop_index("ix_citations_job_id", table_name="citations")
    op.drop_table("citations")
