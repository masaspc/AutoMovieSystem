"""レビュー一覧ページ(仕様§16)。個別承認/却下は app.web.approvals を参照。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.review import Review

router = APIRouter(tags=["web-reviews"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/reviews", response_class=HTMLResponse)
def list_reviews(request: Request, db: DbSession) -> HTMLResponse:
    reviews = db.query(Review).order_by(Review.created_at.desc()).limit(200).all()
    passed_count = sum(1 for r in reviews if r.passed)
    blocking_count = sum(1 for r in reviews if not r.passed)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "reviews/list.html",
        {"reviews": reviews, "passed_count": passed_count, "blocking_count": blocking_count},
    )
