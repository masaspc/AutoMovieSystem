"""Script モデル(仕様§6・§10)。台本1バージョン分。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow_naive
from app.db.base import Base

SCRIPT_STATUSES = ("draft", "reviewed", "rejected")


class Script(Base):
    __tablename__ = "scripts"
    __table_args__ = (UniqueConstraint("topic_id", "version", name="uq_scripts_topic_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    topic_id: Mapped[str] = mapped_column(String(36), ForeignKey("topics.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    hook: Mapped[str | None] = mapped_column(String, nullable=True)
    # 仕様§10の構造化台本データ全体(セクション・タイムコード等)を保持するJSON。
    body: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    conclusion: Mapped[str | None] = mapped_column(String, nullable=True)
    call_to_action: Mapped[str | None] = mapped_column(String, nullable=True)
    # 生成に使った根拠(Evidence)の参照一覧。
    source_manifest: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_micro_usd: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )
