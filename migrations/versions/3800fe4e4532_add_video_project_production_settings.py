"""add video_project production_settings column

Revision ID: 3800fe4e4532
Revises: 8ddd9daa528c
Create Date: 2026-07-12 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3800fe4e4532"
down_revision: Union[str, Sequence[str], None] = "8ddd9daa528c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    `production_settings` はユーザーが選んだ制作方針一式(尺プリセット・台本テンプレート等)。
    nullable・server_defaultなしで追加する(既存行はNULL=デフォルト扱い。
    `app.schemas.production_settings.ProductionSettings` の呼び出し側でNone時にデフォルト
    プリセットを補う)。
    """
    with op.batch_alter_table("video_projects") as batch_op:
        batch_op.add_column(sa.Column("production_settings", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("video_projects") as batch_op:
        batch_op.drop_column("production_settings")
