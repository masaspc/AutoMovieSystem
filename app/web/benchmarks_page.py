"""ベンチマーク(参考チャンネル・動画)ページ(グロース機能)。

他チャンネルの成功動画を登録し、フォーマットを模倣した差別化企画を生成する。
コンテンツの転載は行わない(docs/content-policy.md)。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.csrf import get_or_issue_csrf_token, set_csrf_cookie
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.benchmark_video import BenchmarkVideo
from app.models.channel import Channel
from app.services.growth import derive_topic_from_benchmark, register_benchmark_video
from app.web.common import require_csrf, with_message

logger = get_logger(__name__)

router = APIRouter(tags=["web-benchmarks"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/benchmarks", response_class=HTMLResponse)
def list_benchmarks(request: Request, db: DbSession) -> HTMLResponse:
    benchmarks = db.query(BenchmarkVideo).order_by(BenchmarkVideo.created_at.desc()).all()
    channels = db.query(Channel).order_by(Channel.created_at.asc()).all()

    csrf_token = get_or_issue_csrf_token(request)
    templates = request.app.state.templates
    response = templates.TemplateResponse(
        request,
        "benchmarks/list.html",
        {"benchmarks": benchmarks, "channels": channels, "csrf_token": csrf_token},
    )
    set_csrf_cookie(response, csrf_token)
    return response


@router.post("/benchmarks")
def create_benchmark(
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
    channel_id: Annotated[str, Form()],
    title: Annotated[str, Form()],
    channel_name: Annotated[str, Form()],
    url: Annotated[str, Form()],
    views: Annotated[str, Form()] = "",
    subscribers: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
    format_tags: Annotated[str, Form()] = "",
) -> RedirectResponse:
    require_csrf(request, csrf_token)

    tags = [t.strip() for t in format_tags.split(",") if t.strip()]
    try:
        benchmark, created = register_benchmark_video(
            db,
            channel_id=channel_id,
            title=title.strip(),
            channel_name=channel_name.strip(),
            url=url.strip(),
            views=int(views) if views.strip().isdigit() else None,
            subscribers=int(subscribers) if subscribers.strip().isdigit() else None,
            notes=notes.strip() or None,
            format_tags=tags,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(url=with_message("/benchmarks", error=str(exc)), status_code=303)
    db.commit()

    info = f"ベンチマークを登録しました: {benchmark.title}" if created else "同じURLが登録済みです"
    logger.info("benchmark_registered", benchmark_id=benchmark.id, created=created)
    return RedirectResponse(url=with_message("/benchmarks", info=info), status_code=303)


@router.post("/benchmarks/{benchmark_id}/derive-topic")
def derive_topic(
    benchmark_id: str, request: Request, db: DbSession, csrf_token: Annotated[str, Form()]
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    try:
        topic, created = derive_topic_from_benchmark(db, benchmark_id=benchmark_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(url=with_message("/benchmarks", error=str(exc)), status_code=303)

    db.commit()
    info = (
        f"差別化企画を作成しました: {topic.title}(企画一覧でスコア計算→量産バッチへ)"
        if created
        else "この動画からの企画は作成済みです"
    )
    return RedirectResponse(url=with_message("/benchmarks", info=info), status_code=303)
