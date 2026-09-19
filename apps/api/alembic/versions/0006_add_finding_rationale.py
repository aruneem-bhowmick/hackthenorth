"""add typed proposition rationale to findings

Revision ID: 0006
Revises: 0005
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable is intentionally additive: existing P1 existence/quote findings
    # have no rationale, while P2 proposition findings can supply one.
    op.add_column("findings", sa.Column("rationale", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("findings", "rationale")
