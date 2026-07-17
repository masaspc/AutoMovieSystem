"""add channel editorial policy

Revision ID: d8f4a7c1b2e3
Revises: c47a2f9b1d3e
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8f4a7c1b2e3"
down_revision: str | Sequence[str] | None = "c47a2f9b1d3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("channels") as batch_op:
        batch_op.add_column(sa.Column("editorial_policy", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("channels") as batch_op:
        batch_op.drop_column("editorial_policy")
