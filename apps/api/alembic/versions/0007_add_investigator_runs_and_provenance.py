"""add investigator runs and source provenance

Revision ID: 0007
Revises: 0006
"""

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql


revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "investigator_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("citation_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("searches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fetches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("live_view_url", sa.String(length=2048), nullable=True),
        sa.Column("outcome", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.ForeignKeyConstraint(["citation_id"], ["citations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("citation_id"),
    )
    op.create_index("ix_investigator_runs_citation_id", "investigator_runs", ["citation_id"])

    op.create_table(
        "provenance",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        # Historic records may predate durable source-acquisition metadata.
        # They still receive a provenance record below, with a null URL only
        # where that old metadata is irrecoverable.
        sa.Column("url", sa.String(length=1024), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("method", sa.String(length=64), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("snapshot_ref", sa.String(length=1024), nullable=True),
        sa.Column("session_ref", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id"),
    )
    op.create_index("ix_provenance_source_id", "provenance", ["source_id"])

    # Railway may already contain P1/P2 sources when this additive migration
    # runs. Backfill an auditable record instead of leaving the P3 API null
    # for those sources. New rows are written by the worker at persistence
    # time using the same digest calculation.
    rows: list[dict[str, object]] = []
    if not context.is_offline_mode():
        connection = op.get_bind()
        source_rows = connection.execute(
            sa.text(
                """
                SELECT s.id, s.text, acquisition.source_url, acquisition.retrieved_at
                FROM sources AS s
                LEFT JOIN LATERAL (
                    SELECT source_url, retrieved_at
                    FROM source_acquisitions
                    WHERE source_id = s.id
                    ORDER BY retrieved_at DESC NULLS LAST, id
                    LIMIT 1
                ) AS acquisition ON TRUE
                """
            )
        ).mappings()
        rows = [
            {
                "id": uuid.uuid4(),
                "source_id": source["id"],
                "url": source["source_url"],
                "retrieved_at": source["retrieved_at"] or datetime.now(timezone.utc),
                "method": "courtlistener",
                "sha256": hashlib.sha256((source["text"] or "").encode("utf-8")).hexdigest(),
                "snapshot_ref": None,
                "session_ref": None,
            }
            for source in source_rows
        ]
    if rows:
        provenance = sa.table(
            "provenance",
            sa.column("id", sa.UUID()),
            sa.column("source_id", sa.UUID()),
            sa.column("url", sa.String()),
            sa.column("retrieved_at", sa.DateTime(timezone=True)),
            sa.column("method", sa.String()),
            sa.column("sha256", sa.String()),
            sa.column("snapshot_ref", sa.String()),
            sa.column("session_ref", sa.String()),
        )
        op.bulk_insert(provenance, rows)


def downgrade() -> None:
    op.drop_index("ix_provenance_source_id", table_name="provenance")
    op.drop_table("provenance")
    op.drop_index("ix_investigator_runs_citation_id", table_name="investigator_runs")
    op.drop_table("investigator_runs")
