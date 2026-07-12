"""企画(Topic)一覧・詳細・作成・スコアリング・台本生成・動画プロジェクト作成。"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.csrf import get_or_issue_csrf_token, set_csrf_cookie
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.asset import Asset
from app.models.channel import Channel
from app.models.evidence import Evidence
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.schemas.production_settings import ProductionSettings
from app.services.orchestration import (
    _advance_status,  # noqa: SLF001 - オーケストレーションの該当ステップを再利用する
    _ensure_dummy_evidence,  # noqa: SLF001
    _get_or_create_video_project,  # noqa: SLF001
    link_script_and_advance,
)
from app.services.topics import importer
from app.services.topics.scoring import TopicNotFoundError, score_topic
from app.web.common import require_csrf, with_message
from app.workers.tasks.production import produce_video_task
from app.workers.tasks.scripts import generate_script_task

logger = get_logger(__name__)

router = APIRouter(tags=["web-topics"])

DbSession = Annotated[Session, Depends(get_db)]

PRODUCTION_PRESETS = (
    ("short", "Short（30〜60秒）"),
    ("standard_3min", "標準3分（150〜210秒）"),
    ("standard_5min", "標準5分（270〜330秒）"),
    ("standard_8min", "標準8分（420〜540秒）"),
    ("custom", "カスタム"),
)
SCRIPT_TEMPLATES = (
    ("explainer", "解説"),
    ("ranking", "ランキング"),
    ("problem_solution", "問題解決"),
    ("comparison", "比較"),
    ("story", "ストーリー"),
    ("dialogue", "掛け合い"),
    ("shorts", "Shorts"),
    ("trivia", "雑学"),
    ("news_commentary", "ニュース解説"),
)


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
def topic_detail(
    topic_id: str,
    request: Request,
    db: DbSession,
    task_id: str | None = None,
    task_label: str | None = None,
) -> HTMLResponse:
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
    latest_manifest = scripts[0].source_manifest if scripts else {}
    raw_production_settings = (
        video_project.production_settings
        if video_project and video_project.production_settings
        else (latest_manifest or {}).get("production_settings")
    )
    production_settings = ProductionSettings.model_validate(
        raw_production_settings or ProductionSettings().model_dump()
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
            "production_settings": production_settings,
            "production_presets": PRODUCTION_PRESETS,
            "script_templates": SCRIPT_TEMPLATES,
            "csrf_token": csrf_token,
            "task_id": task_id,
            "task_label": task_label,
        },
    )
    set_csrf_cookie(response, csrf_token)
    return response


def _production_settings_from_form(
    *,
    preset: str,
    target_duration_seconds: int,
    min_duration_seconds: int,
    max_duration_seconds: int,
    target_character_count: int | None,
    min_sections: int,
    max_sections: int,
    speaking_rate: float,
    script_template: str,
    tone: str,
    dialogue_ratio: float,
    bgm_mood: str = "calm",
    bgm_volume_db: float = -19.0,
    se_enabled: bool = True,
    se_volume_db: float = -10.0,
) -> ProductionSettings:
    if preset == "custom":
        values: dict[str, object] = {
            "preset": preset,
            "target_duration_seconds": target_duration_seconds,
            "min_duration_seconds": min_duration_seconds,
            "max_duration_seconds": max_duration_seconds,
            "min_sections": min_sections,
            "max_sections": max_sections,
        }
    else:
        values = ProductionSettings.from_preset(preset).model_dump()  # type: ignore[arg-type]
    values.update(
        {
            "target_character_count": target_character_count,
            "speaking_rate": speaking_rate,
            "script_template": script_template,
            "tone": tone,
            "dialogue_ratio": dialogue_ratio,
            "bgm_mood": bgm_mood,
            "bgm_volume_db": bgm_volume_db,
            "se_enabled": se_enabled,
            "se_volume_db": se_volume_db,
        }
    )
    return ProductionSettings.model_validate(values)


@router.post("/topics/{topic_id}/production-settings")
def save_production_settings(
    topic_id: str,
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
    preset: Annotated[str, Form()],
    target_duration_seconds: Annotated[int, Form()],
    min_duration_seconds: Annotated[int, Form()],
    max_duration_seconds: Annotated[int, Form()],
    min_sections: Annotated[int, Form()],
    max_sections: Annotated[int, Form()],
    speaking_rate: Annotated[float, Form()],
    script_template: Annotated[str, Form()],
    tone: Annotated[str, Form()],
    dialogue_ratio: Annotated[float, Form()],
    intent: Annotated[str, Form()] = "save",
    target_character_count: Annotated[int | None, Form()] = None,
    bgm_mood: Annotated[str, Form()] = "calm",
    bgm_volume_db: Annotated[float, Form()] = -19.0,
    se_enabled: Annotated[bool, Form()] = False,
    se_volume_db: Annotated[float, Form()] = -10.0,
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail=f"Topic not found: {topic_id}")
    try:
        production_settings = _production_settings_from_form(
            preset=preset,
            target_duration_seconds=target_duration_seconds,
            min_duration_seconds=min_duration_seconds,
            max_duration_seconds=max_duration_seconds,
            target_character_count=target_character_count,
            min_sections=min_sections,
            max_sections=max_sections,
            speaking_rate=speaking_rate,
            script_template=script_template,
            tone=tone,
            dialogue_ratio=dialogue_ratio,
            bgm_mood=bgm_mood,
            bgm_volume_db=bgm_volume_db,
            se_enabled=se_enabled,
            se_volume_db=se_volume_db,
        )
    except (ValidationError, ValueError) as exc:
        return RedirectResponse(
            url=with_message(f"/topics/{topic_id}", error=f"制作設定が不正です: {exc}"),
            status_code=303,
        )

    project = _get_or_create_video_project(db, topic_id=topic.id)
    if (
        project.status
        not in {
            "TOPIC_CREATED",
            "TOPIC_SCORED",
            "RESEARCH_READY",
            "SCRIPT_GENERATED",
            "SCRIPT_REVIEWED",
        }
        or db.query(Asset).filter(Asset.video_project_id == project.id).first() is not None
    ):
        return RedirectResponse(
            url=with_message(
                f"/topics/{topic_id}",
                error="素材生成後は制作設定を変更できません。新しい動画プロジェクトで作成してください",
            ),
            status_code=303,
        )
    project.production_settings = production_settings.model_dump()
    _advance_status(project, "TOPIC_SCORED")
    _ensure_dummy_evidence(db, topic_id=topic.id)
    _advance_status(project, "RESEARCH_READY")
    db.commit()

    if intent == "generate":
        task = generate_script_task.delay(topic_id)
        return RedirectResponse(
            url=f"/topics/{topic_id}?task_id={task.id}&task_label=台本生成",
            status_code=303,
        )
    return RedirectResponse(
        url=with_message(f"/topics/{topic_id}", info="制作設定を保存しました"),
        status_code=303,
    )


@router.post("/topics/{topic_id}/generate-script")
def generate_script_route(
    topic_id: str, request: Request, db: DbSession, csrf_token: Annotated[str, Form()]
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail=f"Topic not found: {topic_id}")

    # LLM呼び出しは数分かかることがあるため、リクエストをブロックせずCeleryへ
    # dispatchする。完了はタスク進行状況ポーリング(/tasks/{task_id}/status)経由で
    # 画面へ反映される(台本のVideoProjectへの紐付けはタスク側で行う)。
    task = generate_script_task.delay(topic_id)
    redirect_url = f"/topics/{topic_id}?task_id={task.id}&task_label=台本生成"
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/topics/{topic_id}/produce")
def produce_video_route(
    topic_id: str, request: Request, db: DbSession, csrf_token: Annotated[str, Form()]
) -> RedirectResponse:
    """1クリック一括制作: 台本→素材→音声→レンダリング→自動レビューまで実行する。

    毎日投稿の運用向け(承認・アップロードは人間のまま=fail-closed維持)。
    冪等なので失敗後に再度押すと完了済み工程をスキップして途中から再開する。
    """
    require_csrf(request, csrf_token)
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail=f"Topic not found: {topic_id}")

    task = produce_video_task.delay(topic_id)
    redirect_url = f"/topics/{topic_id}?task_id={task.id}&task_label=一括制作"
    return RedirectResponse(url=redirect_url, status_code=303)


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

    latest_script = (
        db.query(Script)
        .filter(Script.topic_id == topic.id)
        .order_by(Script.version.desc())
        .first()
    )
    if latest_script is not None:
        link_script_and_advance(db, project, latest_script)

    db.commit()

    logger.info("video_project_created_from_topic", topic_id=topic_id, video_project_id=project.id)
    return RedirectResponse(url=f"/video-projects/{project.id}", status_code=303)
