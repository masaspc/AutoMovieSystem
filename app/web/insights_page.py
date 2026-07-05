"""改善提案(Insight)一覧ページ(仕様§16)。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.insight import Insight

router = APIRouter(tags=["web-insights"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/insights", response_class=HTMLResponse)
def list_insights(request: Request, db: DbSession, insight_type: str | None = None) -> HTMLResponse:
    query = db.query(Insight)
    if insight_type:
        query = query.filter(Insight.insight_type == insight_type)
    insights = query.order_by(Insight.created_at.desc()).limit(200).all()

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "insights/list.html",
        {"insights": insights, "insight_type_filter": insight_type or ""},
    )
