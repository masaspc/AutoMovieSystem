"""改善提案(Insight)一覧ページ(仕様§16)。"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.csrf import get_or_issue_csrf_token, set_csrf_cookie
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.insight import Insight
from app.web.common import require_csrf, with_message

logger = get_logger(__name__)

router = APIRouter(tags=["web-insights"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/insights", response_class=HTMLResponse)
def list_insights(request: Request, db: DbSession, insight_type: str | None = None) -> HTMLResponse:
    query = db.query(Insight)
    if insight_type:
        query = query.filter(Insight.insight_type == insight_type)
    insights = query.order_by(Insight.created_at.desc()).limit(200).all()

    csrf_token = get_or_issue_csrf_token(request)
    templates = request.app.state.templates
    response = templates.TemplateResponse(
        request,
        "insights/list.html",
        {
            "insights": insights,
            "insight_type_filter": insight_type or "",
            "csrf_token": csrf_token,
        },
    )
    set_csrf_cookie(response, csrf_token)
    return response


@router.post("/insights/{insight_id}/delete")
def delete_insight(
    insight_id: str,
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
    insight_type: Annotated[str, Form(max_length=64)] = "",
) -> RedirectResponse:
    """Insightを削除する。self_reviewなら以後の台本プロンプト注入から外れる。"""
    require_csrf(request, csrf_token)
    insight = db.get(Insight, insight_id)
    if insight is None:
        raise HTTPException(status_code=404, detail="Insight not found")

    deleted_type = insight.insight_type
    db.delete(insight)
    db.commit()
    logger.info("insight_deleted", insight_id=insight_id, insight_type=deleted_type)

    redirect_url = "/insights"
    if insight_type:
        redirect_url = f"{redirect_url}?{urlencode({'insight_type': insight_type})}"
    return RedirectResponse(
        url=with_message(redirect_url, info="改善提案を削除しました"),
        status_code=303,
    )
