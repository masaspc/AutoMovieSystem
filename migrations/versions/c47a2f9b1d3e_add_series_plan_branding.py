"""add series plan branding

Revision ID: c47a2f9b1d3e
Revises: 9b21f86d4c10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c47a2f9b1d3e"
down_revision: str | Sequence[str] | None = "9b21f86d4c10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("series_plans") as batch_op:
        batch_op.add_column(sa.Column("branding", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("series_plans") as batch_op:
        batch_op.drop_column("branding")
