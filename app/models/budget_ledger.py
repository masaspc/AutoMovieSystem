"""BudgetLedger モデル(ADR-0007)。予算の予約(reserve)/確定(commit)台帳。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow_naive
from app.db.base import Base

PERIOD_TYPES = ("daily", "monthly")


class BudgetLedger(Base):
    __tablename__ = "budget_ledgers"
    __table_args__ = (
        UniqueConstraint("period_type", "period_key", name="uq_budget_ledgers_period"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    period_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # 例: daily -> "2026-07-04", monthly -> "2026-07"
    period_key: Mapped[str] = mapped_column(String(16), nullable=False)

    budget_micro_usd: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_micro_usd: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    committed_micro_usd: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )
