"""add non-verdict GPTZero signals

Revision ID: 0008
Revises: 0007
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the separate, additive store required by CON-SIG-001."""

    op.create_table(
        "signals",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("citation_id", sa.UUID(), nullable=True),
        sa.Column("section_ref", sa.String(length=128), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column(
            "raw",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["citation_id"], ["citations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_signals_job_id", "signals", ["job_id"])
    op.create_index("ix_signals_citation_id", "signals", ["citation_id"])
    op.create_index("ix_signals_section_ref", "signals", ["section_ref"])


def downgrade() -> None:
    op.drop_index("ix_signals_section_ref", table_name="signals")
    op.drop_index("ix_signals_citation_id", table_name="signals")
    op.drop_index("ix_signals_job_id", table_name="signals")
    op.drop_table("signals")
