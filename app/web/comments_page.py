"""コメント一覧ページ(仕様§16)。カテゴリフィルタ+分類内訳サマリー。"""

from __future__ import annotations

from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.comment import Comment

router = APIRouter(tags=["web-comments"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/comments", response_class=HTMLResponse)
def list_comments(request: Request, db: DbSession, category: str | None = None) -> HTMLResponse:
    all_comments = db.query(Comment).all()
    category_summary = dict(Counter(c.category for c in all_comments))

    query = db.query(Comment)
    if category:
        query = query.filter(Comment.category == category)
    comments = query.order_by(Comment.published_at.desc()).limit(200).all()

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "comments/list.html",
        {
            "comments": comments,
            "category_summary": category_summary,
            "category_filter": category or "",
        },
    )
