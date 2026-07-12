"""講座・連作動画の全体方針を保持するSeriesPlan。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow_naive
from app.db.base import Base


class SeriesPlan(Base):
    __tablename__ = "series_plans"
    __table_args__ = (
        UniqueConstraint("channel_id", "name", name="uq_series_plans_channel_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    channel_id: Mapped[str] = mapped_column(String(36), ForeignKey("channels.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_audience: Mapped[str] = mapped_column(String, nullable=False)
    starting_knowledge: Mapped[str] = mapped_column(String, nullable=False)
    final_goal: Mapped[str] = mapped_column(String, nullable=False)
    series_prompt: Mapped[str] = mapped_column(String, nullable=False, default="")
    shared_rules: Mapped[str] = mapped_column(String, nullable=False, default="")
    technology_version: Mapped[str] = mapped_column(
        String(128), nullable=False, default="Python 3.12"
    )
    development_environment: Mapped[str] = mapped_column(
        String(255), nullable=False, default="VS Code"
    )
    planned_episode_count: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    curriculum_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )
