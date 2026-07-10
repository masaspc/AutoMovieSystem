"""JobRun モデル(冪等性基盤)。全ジョブ実行の記録 + idempotency_key 一意制約。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow_naive
from app.db.base import Base

JOB_RUN_STATUSES = ("started", "succeeded", "failed", "skipped")


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="started")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )
