"""投稿(Publication)一覧ページ(仕様§16)。公開予約(即時/日時指定)フォームを提供する。

ゲート拒否(AUTO_PUBLISH_ENABLED=false等)は例外にせず理由をフラッシュ表示する(500にしない)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.csrf import get_or_issue_csrf_token, set_csrf_cookie
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.publication import Publication
from app.providers.youtube.factory import get_youtube_provider
from app.services.publishing.scheduler import PublicationNotFoundError, schedule_publication
from app.web.common import require_csrf, with_message

logger = get_logger(__name__)

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

    csrf_token = get_or_issue_csrf_token(request)
    templates = request.app.state.templates
    response = templates.TemplateResponse(
        request,
        "publications/list.html",
        {
            "publications": publications,
            "upload_status_filter": upload_status or "",
            "csrf_token": csrf_token,
        },
    )
    set_csrf_cookie(response, csrf_token)
    return response


def _parse_scheduled_at(scheduled_at: str | None) -> datetime:
    """datetime-local入力(空なら現在時刻)をタイムゾーン付きdatetimeへ変換する。"""
    if not scheduled_at:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(scheduled_at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid scheduled_at") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


@router.post("/publications/{publication_id}/schedule")
async def schedule_publication_action(
    publication_id: str,
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
    scheduled_at: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    require_csrf(request, csrf_token)

    publish_at = _parse_scheduled_at(scheduled_at)
    provider = get_youtube_provider()

    try:
        result = await schedule_publication(
            db, publication_id=publication_id, publish_at=publish_at, provider=provider
        )
    except PublicationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not result.scheduled:
        db.commit()
        logger.info(
            "publication_schedule_rejected",
            publication_id=publication_id,
            reasons=result.reasons,
        )
        return RedirectResponse(
            url=with_message("/publications", error="; ".join(result.reasons)),
            status_code=303,
        )

    db.commit()
    logger.info("publication_schedule_succeeded", publication_id=publication_id)
    return RedirectResponse(
        url=with_message(
            "/publications",
            info=f"公開予約しました(scheduled_at={publish_at.isoformat()})",
        ),
        status_code=303,
    )
