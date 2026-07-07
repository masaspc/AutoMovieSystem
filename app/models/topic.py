"""Topic モデル(仕様§6)。企画候補の一意な取り込み単位。

`(channel_id, source_type, source_ref)` UNIQUE で取り込み元ごとの自然キーによる
get-or-create を保証する(ADR-0004)。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

SOURCE_TYPES = ("manual", "csv", "rss", "comment", "analytics", "derived", "webhook", "benchmark")
RISK_LEVELS = ("low", "medium", "high")
TOPIC_STATUSES = ("created", "scored", "research_ready", "rejected")


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (
        UniqueConstraint(
            "channel_id", "source_type", "source_ref", name="uq_topics_channel_source"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    channel_id: Mapped[str] = mapped_column(String(36), ForeignKey("channels.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # 取り込み元の自然キー。手動=クライアント生成キー、CSV=行ハッシュ、
    # コメント派生=youtube_comment_id。
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)

    demand_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    revenue_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    originality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    expertise_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    freshness_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    production_cost_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    total_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
