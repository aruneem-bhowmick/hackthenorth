"""add session-scoped extracted brief pages

Revision ID: 0004
Revises: 0003
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("review_token_hash", sa.String(length=64), nullable=True))
    op.create_table(
        "brief_pages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "page", name="uq_brief_pages_job_page"),
    )
    op.create_index("ix_brief_pages_job_id", "brief_pages", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_brief_pages_job_id", table_name="brief_pages")
    op.drop_table("brief_pages")
    op.drop_column("jobs", "review_token_hash")
