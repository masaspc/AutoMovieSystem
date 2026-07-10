"""Insight モデル(仕様§6・§15)。指標/コメントから作る改善提案。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow_naive
from app.db.base import Base


class Insight(Base):
    __tablename__ = "insights"
    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "source_id",
            "insight_type",
            "source_ref",
            name="uq_insights_source_type_id_type_ref",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    insight_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    finding: Mapped[str] = mapped_column(String, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    recommended_action: Mapped[str] = mapped_column(String, nullable=False)
    human_review_reason: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
