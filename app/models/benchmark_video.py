"""BenchmarkVideo モデル(グロース機能)。

他チャンネルの成功動画を「フォーマットの研究・模倣」のために登録する。
コンテンツの転載は行わない(docs/content-policy.md)。URL単位で一意。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow_naive
from app.db.base import Base


class BenchmarkVideo(Base):
    __tablename__ = "benchmark_videos"
    __table_args__ = (
        UniqueConstraint("channel_id", "url", name="uq_benchmark_videos_channel_url"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # 自チャンネル(このベンチマークをどのチャンネル運用の参考にするか)
    channel_id: Mapped[str] = mapped_column(String(36), ForeignKey("channels.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subscribers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 何が優れているか(フック/構成/サムネ文言等)を運用者がメモする
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    # フォーマットタグ(例: ["ランキング", "冒頭結論", "比較表"])
    format_tags: Mapped[list | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
