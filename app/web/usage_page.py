"""UsageRecord一覧+集計、BudgetLedger表示ページ(仕様§16)。"""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.budget_ledger import BudgetLedger
from app.models.usage_record import UsageRecord

router = APIRouter(tags=["web-usage"])

DbSession = Annotated[Session, Depends(get_db)]

BUDGET_WARNING_THRESHOLD = 0.8


def _budget_ratio(ledger: BudgetLedger) -> float:
    if ledger.budget_micro_usd <= 0:
        return 0.0
    return (ledger.committed_micro_usd + ledger.reserved_micro_usd) / ledger.budget_micro_usd


@router.get("/usage", response_class=HTMLResponse)
def usage_page(request: Request, db: DbSession) -> HTMLResponse:
    records = db.query(UsageRecord).order_by(UsageRecord.created_at.desc()).limit(100).all()

    by_day: dict[str, int] = defaultdict(int)
    by_operation: dict[str, int] = defaultdict(int)
    by_model: dict[str, int] = defaultdict(int)
    for record in db.query(UsageRecord).all():
        day_key = record.created_at.strftime("%Y-%m-%d")
        by_day[day_key] += record.estimated_cost_micro_usd
        by_operation[record.operation] += record.estimated_cost_micro_usd
        by_model[record.model] += record.estimated_cost_micro_usd

    ledgers = (
        db.query(BudgetLedger)
        .order_by(BudgetLedger.period_type, BudgetLedger.period_key.desc())
        .limit(30)
        .all()
    )
    ledger_ratios = {ledger.id: _budget_ratio(ledger) for ledger in ledgers}

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "usage/list.html",
        {
            "records": records,
            "by_day": dict(sorted(by_day.items(), reverse=True)),
            "by_operation": dict(by_operation),
            "by_model": dict(by_model),
            "ledgers": ledgers,
            "ledger_ratios": ledger_ratios,
            "budget_warning_threshold": BUDGET_WARNING_THRESHOLD,
        },
    )
