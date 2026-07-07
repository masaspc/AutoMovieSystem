"""成長ダッシュボード(グロース機能)。

登録者目標の進捗・投稿ペース・動画別パフォーマンスを表示し、
勝ち動画からの続編企画作成と量産バッチをここから実行する。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.csrf import get_or_issue_csrf_token, set_csrf_cookie
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.channel import Channel
from app.providers.llm.factory import get_llm_provider
from app.providers.tts.factory import get_tts_provider
from app.providers.youtube.factory import get_youtube_provider
from app.services.growth import (
    compute_growth_summary,
    derive_sequel_topic,
    list_video_performance,
    run_production_batch,
)
from app.services.orchestration import PipelineProviders
from app.web.common import require_csrf, with_message

logger = get_logger(__name__)

router = APIRouter(tags=["web-growth"])

DbSession = Annotated[Session, Depends(get_db)]

_MAX_BATCH_LIMIT = 10


@router.get("/growth", response_class=HTMLResponse)
def growth_dashboard(request: Request, db: DbSession) -> HTMLResponse:
    summary = compute_growth_summary(db)
    performances = list_video_performance(db, limit=20)
    channels = db.query(Channel).order_by(Channel.created_at.asc()).all()

    csrf_token = get_or_issue_csrf_token(request)
    templates = request.app.state.templates
    response = templates.TemplateResponse(
        request,
        "growth/index.html",
        {
            "summary": summary,
            "performances": performances,
            "channels": channels,
            "csrf_token": csrf_token,
        },
    )
    set_csrf_cookie(response, csrf_token)
    return response


@router.post("/growth/production-batch")
async def production_batch(
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
    channel_id: Annotated[str, Form()],
    limit: Annotated[int, Form()] = 3,
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    limit = max(1, min(limit, _MAX_BATCH_LIMIT))

    providers = PipelineProviders(
        llm=get_llm_provider(), tts=get_tts_provider(), youtube=get_youtube_provider()
    )
    try:
        report = await run_production_batch(
            db, channel_id=channel_id, providers=providers, limit=limit
        )
    except Exception as exc:  # noqa: BLE001 - サービス例外をユーザー向けに表示(500にしない)
        db.rollback()
        return RedirectResponse(url=with_message("/growth", error=str(exc)), status_code=303)

    db.commit()
    message = (
        f"量産バッチ完了: {report.attempted}件制作 / レビュー合格{report.review_passed}件"
        + (f" / 失敗{len(report.errors)}件({'; '.join(report.errors)})" if report.errors else "")
        + "。合格分は動画プロジェクトから承認してください。"
    )
    logger.info(
        "production_batch_via_web",
        channel_id=channel_id,
        attempted=report.attempted,
        review_passed=report.review_passed,
    )
    return RedirectResponse(url=with_message("/growth", info=message), status_code=303)


@router.post("/growth/publications/{publication_id}/sequel")
def create_sequel(
    publication_id: str, request: Request, db: DbSession, csrf_token: Annotated[str, Form()]
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    try:
        topic, created = derive_sequel_topic(db, publication_id=publication_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(url=with_message("/growth", error=str(exc)), status_code=303)

    db.commit()
    info = f"続編企画を作成しました: {topic.title}" if created else "続編企画は作成済みです"
    return RedirectResponse(url=with_message("/growth", info=info), status_code=303)
