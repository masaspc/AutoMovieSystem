"""VideoMetricDaily モデル(仕様§6・§14)。Publication単位の日次指標。"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow_naive
from app.db.base import Base


class VideoMetricDaily(Base):
    __tablename__ = "video_metric_daily"
    __table_args__ = (
        UniqueConstraint(
            "publication_id", "metric_date", name="uq_video_metric_daily_publication_date"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    publication_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("publications.id"), nullable=False
    )
    metric_date: Mapped[date] = mapped_column(Date, nullable=False)

    views: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    watch_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    average_view_duration: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    average_view_percentage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ctr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comments_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    subscribers_gained: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    subscribers_lost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_revenue: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )
