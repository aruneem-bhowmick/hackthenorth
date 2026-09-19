"""add normalised quote offsets for P1 quote review

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `quote_start`/`quote_end` remain the original brief offsets from the
    # SPEC.md §7 shape; these additive fields retain normalised processing
    # coordinates without breaking the reversible-ingestion contract.
    op.add_column("claims", sa.Column("quote_processing_start", sa.Integer(), nullable=True))
    op.add_column("claims", sa.Column("quote_processing_end", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("claims", "quote_processing_end")
    op.drop_column("claims", "quote_processing_start")
