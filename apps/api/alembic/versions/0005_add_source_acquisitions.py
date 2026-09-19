"""add durable quote-source acquisition state

Revision ID: 0005
Revises: 0004
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "source_acquisitions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("cluster_id", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cluster_id"),
    )
    op.add_column("citations", sa.Column("source_acquisition_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_citations_source_acquisition_id", "citations", "source_acquisitions", ["source_acquisition_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_citations_source_acquisition_id", "citations", ["source_acquisition_id"])


def downgrade() -> None:
    op.drop_index("ix_citations_source_acquisition_id", table_name="citations")
    op.drop_constraint("fk_citations_source_acquisition_id", "citations", type_="foreignkey")
    op.drop_column("citations", "source_acquisition_id")
    op.drop_table("source_acquisitions")
