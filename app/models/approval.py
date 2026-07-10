"""Approval モデル(仕様§6)。人間による承認/却下の記録(監査ログ)。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow_naive
from app.db.base import Base

APPROVAL_DECISIONS = ("approved", "rejected")


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    video_project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("video_projects.id"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
