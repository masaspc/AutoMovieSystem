"""投稿(Publication)一覧ページ(仕様§16)。公開予約は表示のみ。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.publication import Publication

router = APIRouter(tags=["web-publications"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/publications", response_class=HTMLResponse)
def list_publications(
    request: Request, db: DbSession, upload_status: str | None = None
) -> HTMLResponse:
    query = db.query(Publication)
    if upload_status:
        query = query.filter(Publication.upload_status == upload_status)
    publications = query.order_by(Publication.created_at.desc()).all()

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "publications/list.html",
        {"publications": publications, "upload_status_filter": upload_status or ""},
    )
