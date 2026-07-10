"""企画(Topic)一覧・詳細・作成・スコアリング・台本生成・動画プロジェクト作成。"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.csrf import get_or_issue_csrf_token, set_csrf_cookie
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.channel import Channel
from app.models.evidence import Evidence
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.providers.llm.factory import get_llm_provider
from app.services.jobs import JobInProgressError
from app.services.orchestration import (
    _advance_status,  # noqa: SLF001 - オーケストレーションの該当ステップを再利用する
    _ensure_dummy_evidence,  # noqa: SLF001
    _get_or_create_video_project,  # noqa: SLF001
)
from app.services.scripts.generator import TopicNotFoundError as ScriptTopicNotFoundError
from app.services.scripts.generator import generate_script
from app.services.topics import importer
from app.services.topics.scoring import TopicNotFoundError, score_topic
from app.web.common import require_csrf, with_message

logger = get_logger(__name__)

router = APIRouter(tags=["web-topics"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/topics", response_class=HTMLResponse)
def list_topics(
    request: Request,
    db: DbSession,
    status: str | None = None,
    channel_id: str | None = None,
) -> HTMLResponse:
    query = db.query(Topic)
    if status:
        query = query.filter(Topic.status == status)
    if channel_id:
        query = query.filter(Topic.channel_id == channel_id)
    topics = query.order_by(Topic.total_score.desc()).all()
    channels = db.query(Channel).order_by(Channel.name).all()

    csrf_token = get_or_issue_csrf_token(request)
    templates = request.app.state.templates
    response = templates.TemplateResponse(
        request,
        "topics/list.html",
        {
            "topics": topics,
            "channels": channels,
            "status_filter": status or "",
            "channel_filter": channel_id or "",
            "csrf_token": csrf_token,
        },
    )
    set_csrf_cookie(response, csrf_token)
    return response


@router.post("/topics")
def create_topic(
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
    channel_id: Annotated[str, Form()],
    title: Annotated[str, Form()],
    description: Annotated[str | None, Form()] = None,
    client_key: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    key = client_key or secrets.token_hex(8)
    importer.create_manual_topic(
        db, channel_id=channel_id, title=title, description=description, client_key=key
    )
    db.commit()
    return RedirectResponse(url="/topics", status_code=303)


@router.post("/topics/import")
def import_topics(
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
    channel_id: Annotated[str, Form()],
    csv_text: Annotated[str, Form()],
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    try:
        result = importer.import_topics_csv(db, channel_id=channel_id, csv_text=csv_text)
    except importer.InvalidCsvError as exc:
        db.rollback()
        return RedirectResponse(url=with_message("/topics", error=str(exc)), status_code=303)
    db.commit()
    return RedirectResponse(
        url=with_message(
            "/topics",
            info=f"取り込み: {result.created}件作成 / {result.skipped}件スキップ",
        ),
        status_code=303,
    )


@router.post("/topics/{topic_id}/score")
def score_topic_route(
    topic_id: str, request: Request, db: DbSession, csrf_token: Annotated[str, Form()]
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    try:
        score_topic(db, topic_id)
    except TopicNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return RedirectResponse(url="/topics", status_code=303)


@router.get("/topics/{topic_id}", response_class=HTMLResponse)
def topic_detail(topic_id: str, request: Request, db: DbSession) -> HTMLResponse:
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail=f"Topic not found: {topic_id}")

    evidence_list = db.query(Evidence).filter(Evidence.topic_id == topic_id).all()
    scripts = (
        db.query(Script).filter(Script.topic_id == topic_id).order_by(Script.version.desc()).all()
    )
    video_project = (
        db.query(VideoProject)
        .filter(VideoProject.topic_id == topic_id)
        .order_by(VideoProject.generation.desc())
        .first()
    )

    csrf_token = get_or_issue_csrf_token(request)
    templates = request.app.state.templates
    response = templates.TemplateResponse(
        request,
        "topics/detail.html",
        {
            "topic": topic,
            "evidence_list": evidence_list,
            "scripts": scripts,
            "video_project": video_project,
            "csrf_token": csrf_token,
        },
    )
    set_csrf_cookie(response, csrf_token)
    return response


@router.post("/topics/{topic_id}/generate-script")
async def generate_script_route(
    topic_id: str, request: Request, db: DbSession, csrf_token: Annotated[str, Form()]
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    try:
        script = await generate_script(db, topic_id=topic_id, provider=get_llm_provider())
    except ScriptTopicNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobInProgressError:
        db.rollback()
        return RedirectResponse(
            url=with_message(f"/topics/{topic_id}", error="実行中です"), status_code=303
        )
    except Exception as exc:  # noqa: BLE001 - 予算超過・LLMスキーマ不正等をユーザー向けに表示する
        db.commit()
        return RedirectResponse(
            url=with_message(f"/topics/{topic_id}", error=str(exc)), status_code=303
        )

    projects = db.query(VideoProject).filter(VideoProject.topic_id == topic_id).all()
    for project in projects:
        if project.script_id is None:
            project.script_id = script.id
    db.commit()
    return RedirectResponse(url=f"/topics/{topic_id}", status_code=303)


@router.post("/topics/{topic_id}/create-video-project")
def create_video_project_route(
    topic_id: str, request: Request, db: DbSession, csrf_token: Annotated[str, Form()]
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail=f"Topic not found: {topic_id}")

    project = _get_or_create_video_project(db, topic_id=topic.id)
    _advance_status(project, "TOPIC_SCORED")
    _ensure_dummy_evidence(db, topic_id=topic.id)
    _advance_status(project, "RESEARCH_READY")
    db.commit()

    logger.info("video_project_created_from_topic", topic_id=topic_id, video_project_id=project.id)
    return RedirectResponse(url=f"/video-projects/{project.id}", status_code=303)
