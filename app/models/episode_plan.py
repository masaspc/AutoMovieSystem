"""シリーズ内の各動画の学習設計を保持するEpisodePlan。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow_naive
from app.db.base import Base


class EpisodePlan(Base):
    __tablename__ = "episode_plans"
    __table_args__ = (
        UniqueConstraint("series_plan_id", "position", name="uq_episode_plans_series_position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    series_plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("series_plans.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(String, nullable=False)
    learning_objectives: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    prerequisite_positions: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    new_concepts: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    review_concepts: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    excluded_concepts: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    demo_outline: Mapped[str] = mapped_column(String, nullable=False, default="")
    exercise_outline: Mapped[str] = mapped_column(String, nullable=False, default="")
    next_episode_bridge: Mapped[str] = mapped_column(String, nullable=False, default="")
    target_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    topic_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("topics.id"), nullable=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )
