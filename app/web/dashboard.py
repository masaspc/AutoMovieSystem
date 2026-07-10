"""ダッシュボード(仕様§16 1ページ目)。集計表示のみ(変更系なし)。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.core.timeutil import utcnow_naive
from app.db.session import get_db
from app.models.budget_ledger import BudgetLedger
from app.models.insight import Insight
from app.models.publication import Publication
from app.models.topic import Topic
from app.models.usage_record import UsageRecord
from app.models.video_project import VideoProject

router = APIRouter(tags=["web-dashboard"])

DbSession = Annotated[Session, Depends(get_db)]

BUDGET_WARNING_THRESHOLD = 0.8


def _status_counts(session: Session, column: InstrumentedAttribute[str]) -> dict[str, int]:
    rows = session.query(column, func.count()).group_by(column).all()
    counts: dict[str, int] = {}
    for status, count in rows:
        counts[status] = count
    return counts


def _budget_ratio(ledger: BudgetLedger | None) -> float:
    if ledger is None or ledger.budget_micro_usd <= 0:
        return 0.0
    return (ledger.committed_micro_usd + ledger.reserved_micro_usd) / ledger.budget_micro_usd


def _sum_cost_micro_usd(session: Session, *, start: datetime, end: datetime) -> int:
    total = (
        session.query(func.coalesce(func.sum(UsageRecord.estimated_cost_micro_usd), 0))
        .filter(UsageRecord.created_at >= start, UsageRecord.created_at < end)
        .scalar()
    )
    return int(total or 0)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: DbSession) -> HTMLResponse:
    topic_counts = _status_counts(db, Topic.status)
    project_counts = _status_counts(db, VideoProject.status)

    latest_publication = db.query(Publication).order_by(Publication.created_at.desc()).first()
    latest_insight = db.query(Insight).order_by(Insight.created_at.desc()).first()

    today = utcnow_naive().date()
    day_start = datetime(today.year, today.month, today.day)
    next_day_start = day_start + timedelta(days=1)
    month_start = datetime(today.year, today.month, 1)
    if today.month == 12:
        next_month_start = datetime(today.year + 1, 1, 1)
    else:
        next_month_start = datetime(today.year, today.month + 1, 1)

    today_cost_micro_usd = _sum_cost_micro_usd(db, start=day_start, end=next_day_start)
    month_cost_micro_usd = _sum_cost_micro_usd(db, start=month_start, end=next_month_start)

    daily_ledger = (
        db.query(BudgetLedger)
        .filter(
            BudgetLedger.period_type == "daily",
            BudgetLedger.period_key == today.strftime("%Y-%m-%d"),
        )
        .one_or_none()
    )
    monthly_ledger = (
        db.query(BudgetLedger)
        .filter(
            BudgetLedger.period_type == "monthly",
            BudgetLedger.period_key == today.strftime("%Y-%m"),
        )
        .one_or_none()
    )
    daily_ratio = _budget_ratio(daily_ledger)
    monthly_ratio = _budget_ratio(monthly_ledger)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "topic_counts": topic_counts,
            "project_counts": project_counts,
            "latest_publication": latest_publication,
            "latest_insight": latest_insight,
            "today_cost_usd": today_cost_micro_usd / 1_000_000,
            "month_cost_usd": month_cost_micro_usd / 1_000_000,
            "daily_ratio": daily_ratio,
            "monthly_ratio": monthly_ratio,
            "budget_warning_threshold": BUDGET_WARNING_THRESHOLD,
        },
    )
