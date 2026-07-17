"""トレンド一覧とニュース解説Shortのワンクリック制作。"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.csrf import get_or_issue_csrf_token, set_csrf_cookie
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.channel import Channel
from app.providers.trends.factory import get_trend_provider
from app.services.trends.service import instant_videoize, settings_for_channel_trends
from app.web.common import require_csrf, with_message
from app.workers.tasks.production import produce_video_task

logger = get_logger(__name__)

router = APIRouter(tags=["web-trends"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/trends", response_class=HTMLResponse)
async def list_trends(request: Request, db: DbSession, channel_id: str = "") -> HTMLResponse:
    """設定済みProviderから最新見出しを取得して表示する。"""
    settings = get_settings()
    channels = db.query(Channel).order_by(Channel.name.asc()).all()
    selected_channel = None
    if channel_id:
        selected_channel = db.get(Channel, channel_id)
        if selected_channel is None:
            raise HTTPException(status_code=400, detail="指定されたチャンネルが見つかりません")
    elif channels:
        selected_channel = channels[0]
    provider_settings = settings_for_channel_trends(selected_channel, settings)
    provider = get_trend_provider(provider_settings)
    items = await provider.fetch_latest(limit=settings.TREND_FETCH_LIMIT)

    csrf_token = get_or_issue_csrf_token(request)
    templates = request.app.state.templates
    response = templates.TemplateResponse(
        request,
        "trends/list.html",
        {
            "items": items,
            "channels": channels,
            "provider_name": settings.TREND_PROVIDER,
            "selected_channel": selected_channel,
            "csrf_token": csrf_token,
        },
    )
    set_csrf_cookie(response, csrf_token)
    return response


@router.post("/trends/videoize")
def videoize_trend(
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
    channel_id: Annotated[str, Form(max_length=36)],
    title: Annotated[str, Form(min_length=1, max_length=255)],
    url: Annotated[str, Form(min_length=1, max_length=2048)],
    summary: Annotated[str, Form(max_length=200)] = "",
    source: Annotated[str, Form(max_length=255)] = "",
) -> RedirectResponse:
    """記事をTopic化し、自動レビューまでの一括制作をdispatchする。"""
    require_csrf(request, csrf_token)
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=400, detail="指定されたチャンネルが見つかりません")

    try:
        topic = instant_videoize(
            db,
            channel_id=channel.id,
            title=title,
            url=url,
            summary=summary,
            source=source,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(url=with_message("/trends", error=str(exc)), status_code=303)
    db.commit()

    task = produce_video_task.delay(topic.id)
    logger.info(
        "trend_videoize_dispatched",
        topic_id=topic.id,
        channel_id=channel.id,
        task_id=task.id,
    )
    query = urlencode({"task_id": task.id, "task_label": "トレンド一括制作"})
    return RedirectResponse(url=f"/topics/{topic.id}?{query}", status_code=303)
