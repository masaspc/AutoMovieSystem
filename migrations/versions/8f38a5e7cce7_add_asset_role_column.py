"""add asset role column

Revision ID: 8f38a5e7cce7
Revises: cbff57302eb3
Create Date: 2026-07-05 19:11:22.369897

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8f38a5e7cce7'
down_revision: Union[str, Sequence[str], None] = 'cbff57302eb3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _derive_role(asset_type: str, meta: dict, asset_id: str) -> str:
    """旧 meta JSON からアプリが検索に使う意味的な role を導出する。

    アプリ側の検索キーと一致させる: "background" / "audio:{section_index}" /
    "subtitle:srt" / "subtitle:vtt" / "endcard"。導出できない行は id へ
    フォールバックし UNIQUE 制約違反だけは避ける。
    """
    meta = meta or {}
    meta_role = meta.get("role")
    if meta_role == "background" or (asset_type == "image" and meta_role is None):
        return "background"
    section_index = meta.get("section_index")
    if asset_type == "audio" and section_index is not None:
        return f"audio:{int(section_index)}"
    kind = meta.get("kind") or meta.get("format")
    if asset_type == "subtitle" and kind in ("srt", "vtt"):
        return f"subtitle:{kind}"
    if asset_type == "endcard" or meta_role == "endcard":
        return "endcard"
    if isinstance(meta_role, str) and meta_role:
        return meta_role
    return asset_id


def upgrade() -> None:
    """Upgrade schema.

    `role` は Asset の同一性を保証する列(UNIQUE(video_project_id, role))。
    安全な手順: nullable で追加 -> 既存行を meta から意味的にbackfill -> NOT NULL化
    -> UNIQUE制約追加(既存DBのレンダリング再開でも背景等を見つけられるようにする)。
    """
    import json

    with op.batch_alter_table("assets") as batch_op:
        batch_op.add_column(sa.Column("role", sa.String(length=64), nullable=True))

    # 既存行を meta JSON から意味的にbackfill(SQLite/PG両対応のためPythonで行単位に処理)。
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, asset_type, meta FROM assets WHERE role IS NULL")
    ).fetchall()
    seen: set[tuple[str, str]] = set()
    for row in rows:
        raw_meta = row[2]
        meta = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta or "{}")
        vp_row = conn.execute(
            sa.text("SELECT video_project_id FROM assets WHERE id = :id"), {"id": row[0]}
        ).fetchone()
        role = _derive_role(row[1], meta, row[0])
        key = (vp_row[0] if vp_row else "", role)
        if key in seen:
            role = row[0]  # 同一project内で衝突したらidへフォールバック
        seen.add(key)
        conn.execute(
            sa.text("UPDATE assets SET role = :role WHERE id = :id"),
            {"role": role, "id": row[0]},
        )

    with op.batch_alter_table("assets") as batch_op:
        batch_op.alter_column("role", existing_type=sa.String(length=64), nullable=False)
        batch_op.create_unique_constraint(
            "uq_assets_video_project_id_role", ["video_project_id", "role"]
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("assets") as batch_op:
        batch_op.drop_constraint("uq_assets_video_project_id_role", type_="unique")
        batch_op.drop_column("role")
