"""Review モデル(仕様§6・§12)。VideoProjectに対する機械/コンテンツ/人間レビュー結果。

`(video_project_id, reviewer_type, review_version)` UNIQUE により、再レビュー時は
新しい review_version として追加する(既存行の上書きはしない: 監査証跡を残す)。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow_naive
from app.db.base import Base

REVIEWER_TYPES = ("machine", "content", "human")


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint(
            "video_project_id",
            "reviewer_type",
            "review_version",
            name="uq_reviews_video_project_reviewer_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    video_project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("video_projects.id"), nullable=False
    )
    reviewer_type: Mapped[str] = mapped_column(String(16), nullable=False)
    review_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # [{code, severity, message, detail}] 形式(app/services/reviews/findings.py の Finding互換)。
    findings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # findings のうち severity=="blocking" のみを抜粋したもの(ゲート判定を高速化するため)。
    blocking_findings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
