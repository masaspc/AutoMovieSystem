"""UsageRecord モデル。全LLM/TTS呼び出しの計上記録(ADR-0004・ADR-0007)。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow_naive
from app.db.base import Base


class UsageRecord(Base):
    __tablename__ = "usage_records"
    __table_args__ = (UniqueConstraint("job_run_id", "seq", name="uq_usage_records_job_run_seq"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_micro_usd: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    related_entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    related_entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    job_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("job_runs.id"), nullable=True
    )
    # job_run_id が非NULLの場合、同一JobRun内での記録順(UNIQUE(job_run_id, seq))。
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
