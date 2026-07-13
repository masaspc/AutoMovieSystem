"""動画プロジェクト一覧・詳細・パイプライン進行ボタン・動画プレビュー配信。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.auth import require_admin
from app.core.config import get_settings
from app.core.csrf import get_or_issue_csrf_token, set_csrf_cookie
from app.core.logging import get_logger
from app.core.paths import resolve_generated_path
from app.db.session import get_db
from app.models.approval import Approval
from app.models.asset import Asset, asset_role_for_audio_section
from app.models.comment import Comment
from app.models.evidence import Evidence
from app.models.insight import Insight
from app.models.publication import Publication
from app.models.review import Review
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_metric_daily import VideoMetricDaily
from app.models.video_project import VideoProject
from app.schemas.production_settings import ProductionSettings
from app.schemas.script_content import ScriptContent
from app.services.media import thumbnails
from app.services.media.dialogue import dialogue_script_enabled, extract_speech_lines
from app.services.scripts.duration import estimate_duration_seconds
from app.services.scripts.editor import ScriptEditError, save_edited_script
from app.web.common import require_csrf, with_message
from app.workers.tasks.feedback import run_self_review_task
from app.workers.tasks.media import (
    prepare_assets_task,
    render_video_task,
    synthesize_audio_task,
)
from app.workers.tasks.publishing import upload_video_task
from app.workers.tasks.reviews import run_automated_review_task
from app.workers.tasks.scripts import regenerate_section_task

logger = get_logger(__name__)

router = APIRouter(tags=["web-video-projects"])

DbSession = Annotated[Session, Depends(get_db)]
_SCRIPT_EDITABLE_STATUSES = {"RESEARCH_READY", "SCRIPT_GENERATED", "SCRIPT_REVIEWED"}


def _latest_reviews(session: Session, video_project_id: str) -> list[Review]:
    reviews = (
        session.query(Review)
        .filter(Review.video_project_id == video_project_id)
        .order_by(Review.reviewer_type, Review.review_version.desc())
        .all()
    )
    seen: set[str] = set()
    latest: list[Review] = []
    for review in reviews:
        if review.reviewer_type in seen:
            continue
        seen.add(review.reviewer_type)
        latest.append(review)
    return latest


@router.get("/video-projects", response_class=HTMLResponse)
def list_video_projects(request: Request, db: DbSession, status: str | None = None) -> HTMLResponse:
    query = db.query(VideoProject)
    if status:
        query = query.filter(VideoProject.status == status)
    projects = query.order_by(VideoProject.updated_at.desc()).all()

    topics_by_id = {t.id: t for t in db.query(Topic).all()}

    # CSRF Cookieの発行方針を詳細画面と統一する(一覧から直接詳細のフォーム操作へ
    # 進んでもCookieが未発行にならないよう、共通ヘルパーで同一属性のCookieを発行)。
    csrf_token = get_or_issue_csrf_token(request)
    templates = request.app.state.templates
    response = templates.TemplateResponse(
        request,
        "video_projects/list.html",
        {"projects": projects, "topics_by_id": topics_by_id, "status_filter": status or ""},
    )
    set_csrf_cookie(response, csrf_token)
    return response


def _next_pipeline_action(db: Session, project: VideoProject) -> tuple[str, str] | None:
    """(表示ラベル, action識別子) を返す。実行可能な次ステップがなければNone。"""
    if project.status == "SCRIPT_REVIEWED":
        return "素材準備", "prepare-assets"
    if project.status == "ASSETS_READY":
        script = db.get(Script, project.script_id) if project.script_id else None
        speech_lines = extract_speech_lines(script.body or {}) if script else []
        expected_roles = [asset_role_for_audio_section(index) for index in range(len(speech_lines))]
        audio_count = (
            db.query(Asset)
            .filter(
                Asset.video_project_id == project.id,
                Asset.asset_type == "audio",
                Asset.role.in_(expected_roles),
            )
            .count()
        )
        if audio_count < len(speech_lines):
            return "音声合成", "synthesize-audio"
        return "レンダリング", "render"
    if project.status == "VIDEO_RENDERED":
        return "自動レビュー", "review"
    if project.status == "UPLOAD_READY":
        return "アップロード", "upload"
    return None


@router.get("/video-projects/{video_project_id}", response_class=HTMLResponse)
def video_project_detail(
    video_project_id: str,
    request: Request,
    db: DbSession,
    task_id: str | None = None,
    task_label: str | None = None,
) -> HTMLResponse:
    project = db.get(VideoProject, video_project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"VideoProject not found: {video_project_id}")

    topic = db.get(Topic, project.topic_id)
    script = db.get(Script, project.script_id) if project.script_id else None
    evidence_list = (
        db.query(Evidence).filter(Evidence.topic_id == project.topic_id).all() if topic else []
    )

    assets = db.query(Asset).filter(Asset.video_project_id == project.id).all()
    thumbnail_candidates = sorted(
        (a for a in assets if a.role.startswith(thumbnails.THUMBNAIL_ROLE_PREFIX)),
        key=lambda a: a.role,
    )
    selected_thumbnail = next(
        (a for a in assets if a.role == thumbnails.THUMBNAIL_ROLE_SELECTED), None
    )
    reviews = _latest_reviews(db, project.id)
    blocking_findings = [f for r in reviews for f in (r.blocking_findings or [])]

    approval = (
        db.query(Approval)
        .filter(Approval.video_project_id == project.id)
        .order_by(Approval.created_at.desc())
        .first()
    )
    publication = (
        db.query(Publication)
        .filter(Publication.video_project_id == project.id)
        .order_by(Publication.created_at.desc())
        .first()
    )
    metrics: list[VideoMetricDaily] = []
    comments: list[Comment] = []
    insights: list[Insight] = []
    if publication is not None:
        metrics = (
            db.query(VideoMetricDaily)
            .filter(VideoMetricDaily.publication_id == publication.id)
            .order_by(VideoMetricDaily.metric_date.desc())
            .all()
        )
        comments = (
            db.query(Comment)
            .filter(Comment.publication_id == publication.id)
            .order_by(Comment.published_at.desc())
            .limit(50)
            .all()
        )
        insights = (
            db.query(Insight)
            .filter(Insight.source_type == "publication", Insight.source_id == publication.id)
            .order_by(Insight.created_at.desc())
            .all()
        )

    media_available = bool(
        project.output_path and project.checksum and Path(project.output_path).exists()
    )
    next_action = _next_pipeline_action(db, project)
    production_settings = ProductionSettings.model_validate(
        project.production_settings or ProductionSettings().model_dump()
    )
    try:
        validated_content = ScriptContent.model_validate(script.body) if script else None
    except ValueError:
        validated_content = None
    estimated_duration_seconds = (
        estimate_duration_seconds(
            validated_content,
            production_settings,
            dialogue_enabled=dialogue_script_enabled(),
        )
        if validated_content
        else 0.0
    )
    script_editable = bool(
        validated_content and project.status in _SCRIPT_EDITABLE_STATUSES and not assets
    )

    csrf_token = get_or_issue_csrf_token(request)
    templates = request.app.state.templates
    response = templates.TemplateResponse(
        request,
        "video_projects/detail.html",
        {
            "project": project,
            "topic": topic,
            "script": script,
            "evidence_list": evidence_list,
            "assets": assets,
            "thumbnail_candidates": thumbnail_candidates,
            "selected_thumbnail": selected_thumbnail,
            "reviews": reviews,
            "blocking_findings": blocking_findings,
            "approval": approval,
            "publication": publication,
            "metrics": metrics,
            "comments": comments,
            "insights": insights,
            "media_available": media_available,
            "next_action": next_action,
            "production_settings": production_settings,
            "estimated_duration_seconds": estimated_duration_seconds,
            "script_editable": script_editable,
            "dialogue_script_enabled": dialogue_script_enabled(),
            "csrf_token": csrf_token,
            "task_id": task_id,
            "task_label": task_label,
        },
    )
    set_csrf_cookie(response, csrf_token)
    return response


def _editable_project_and_script(
    db: Session, video_project_id: str
) -> tuple[VideoProject, Script, ProductionSettings]:
    project = db.get(VideoProject, video_project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"VideoProject not found: {video_project_id}")
    script = db.get(Script, project.script_id) if project.script_id else None
    if script is None:
        raise HTTPException(status_code=409, detail="編集できる台本がありません")
    has_assets = db.query(Asset).filter(Asset.video_project_id == project.id).first() is not None
    if project.status not in _SCRIPT_EDITABLE_STATUSES or has_assets:
        raise HTTPException(
            status_code=409,
            detail="素材生成後の台本は編集できません。新しい動画プロジェクトで再生成してください",
        )
    settings = ProductionSettings.model_validate(
        project.production_settings or ProductionSettings().model_dump()
    )
    return project, script, settings


def _save_script_body(
    db: Session,
    *,
    project: VideoProject,
    script: Script,
    settings: ProductionSettings,
    body: dict,
    reason: str,
) -> Script:
    edited = save_edited_script(
        db,
        source=script,
        body=body,
        production_settings=settings,
        edit_reason=reason,
        dialogue_enabled=dialogue_script_enabled(),
    )
    project.script_id = edited.id
    return edited


@router.post("/video-projects/{video_project_id}/script/sections/{section_index}/update")
def update_script_section(
    video_project_id: str,
    section_index: int,
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
    heading: Annotated[str, Form(min_length=1, max_length=500)],
    narration: Annotated[str, Form(min_length=1, max_length=50_000)],
    visual_instruction: Annotated[str, Form(min_length=1, max_length=2_000)],
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    project, script, settings = _editable_project_and_script(db, video_project_id)
    body = deepcopy(script.body)
    sections = body.get("sections") or []
    if not 0 <= section_index < len(sections):
        raise HTTPException(status_code=404, detail="section not found")
    sections[section_index].update(
        {
            "heading": heading.strip(),
            "narration": narration.strip(),
            "visual_instruction": visual_instruction.strip(),
        }
    )
    try:
        _save_script_body(
            db,
            project=project,
            script=script,
            settings=settings,
            body=body,
            reason="section_update",
        )
    except (ScriptEditError, ValueError) as exc:
        db.rollback()
        return _redirect_back(video_project_id, error=str(exc))
    db.commit()
    return _redirect_back(video_project_id, info="セクションを更新しました")


@router.post("/video-projects/{video_project_id}/script/sections/add")
def add_script_section(
    video_project_id: str,
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
    heading: Annotated[str, Form(min_length=1, max_length=500)],
    narration: Annotated[str, Form(min_length=1, max_length=50_000)],
    visual_instruction: Annotated[str, Form(min_length=1, max_length=2_000)],
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    project, script, settings = _editable_project_and_script(db, video_project_id)
    body = deepcopy(script.body)
    (body.setdefault("sections", [])).append(
        {
            "heading": heading.strip(),
            "narration": narration.strip(),
            "visual_instruction": visual_instruction.strip(),
            "evidence_ids": [],
            "dialogue": [],
        }
    )
    try:
        _save_script_body(
            db, project=project, script=script, settings=settings, body=body, reason="section_add"
        )
    except (ScriptEditError, ValueError) as exc:
        db.rollback()
        return _redirect_back(video_project_id, error=str(exc))
    db.commit()
    return _redirect_back(video_project_id, info="セクションを追加しました")


@router.post("/video-projects/{video_project_id}/script/sections/{section_index}/move")
def move_script_section(
    video_project_id: str,
    section_index: int,
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
    direction: Annotated[str, Form()],
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    project, script, settings = _editable_project_and_script(db, video_project_id)
    body = deepcopy(script.body)
    sections = body.get("sections") or []
    target = section_index - 1 if direction == "up" else section_index + 1
    if not (0 <= section_index < len(sections) and 0 <= target < len(sections)):
        return _redirect_back(video_project_id, error="これ以上移動できません")
    sections[section_index], sections[target] = sections[target], sections[section_index]
    _save_script_body(
        db, project=project, script=script, settings=settings, body=body, reason="section_move"
    )
    db.commit()
    return _redirect_back(video_project_id, info="セクションを並べ替えました")


@router.post("/video-projects/{video_project_id}/script/sections/{section_index}/delete")
def delete_script_section(
    video_project_id: str,
    section_index: int,
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    project, script, settings = _editable_project_and_script(db, video_project_id)
    body = deepcopy(script.body)
    sections = body.get("sections") or []
    if len(sections) <= 1:
        return _redirect_back(video_project_id, error="最後のセクションは削除できません")
    if not 0 <= section_index < len(sections):
        raise HTTPException(status_code=404, detail="section not found")
    sections.pop(section_index)
    _save_script_body(
        db, project=project, script=script, settings=settings, body=body, reason="section_delete"
    )
    db.commit()
    return _redirect_back(video_project_id, info="セクションを削除しました")


@router.post("/video-projects/{video_project_id}/script/sections/{section_index}/regenerate")
def regenerate_script_section_route(
    video_project_id: str,
    section_index: int,
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
    instruction: Annotated[str, Form(max_length=1_000)] = "",
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    _editable_project_and_script(db, video_project_id)
    task = regenerate_section_task.delay(video_project_id, section_index, instruction.strip())
    return _dispatch_task(video_project_id, task.id, "セクション再生成")


def _redirect_back(
    video_project_id: str, *, error: str | None = None, info: str | None = None
) -> RedirectResponse:
    url = with_message(f"/video-projects/{video_project_id}", error=error, info=info)
    return RedirectResponse(url=url, status_code=303)


def _dispatch_task(video_project_id: str, task_id: str, label: str) -> RedirectResponse:
    """パイプラインの各ステップをCeleryへdispatchした後、進行状況ポーリング付きで
    動画プロジェクト詳細ページへ戻る(`/tasks/{task_id}/status` が完了を検知する)。
    """
    url = f"/video-projects/{video_project_id}?task_id={task_id}&task_label={label}"
    return RedirectResponse(url=url, status_code=303)


def _require_video_project(video_project_id: str, db: Session) -> None:
    if db.get(VideoProject, video_project_id) is None:
        raise HTTPException(status_code=404, detail=f"VideoProject not found: {video_project_id}")


@router.post("/video-projects/{video_project_id}/self-review")
def video_project_self_review(
    video_project_id: str,
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
) -> RedirectResponse:
    """詳細画面から、この動画のPublicationを自己レビューする。"""
    require_csrf(request, csrf_token)
    _require_video_project(video_project_id, db)
    publication = (
        db.query(Publication)
        .filter(
            Publication.video_project_id == video_project_id,
            Publication.upload_status == "completed",
            Publication.youtube_video_id.is_not(None),
        )
        .order_by(Publication.created_at.desc())
        .first()
    )
    if publication is None:
        raise HTTPException(status_code=409, detail="投稿後に自己レビューを実行できます")

    task = run_self_review_task.delay(publication.id)
    logger.info(
        "video_project_self_review_dispatched",
        video_project_id=video_project_id,
        publication_id=publication.id,
        task_id=task.id,
    )
    return _dispatch_task(video_project_id, task.id, "自己レビュー")


@router.post("/video-projects/{video_project_id}/pipeline/prepare-assets")
def pipeline_prepare_assets(
    video_project_id: str, request: Request, db: DbSession, csrf_token: Annotated[str, Form()]
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    _require_video_project(video_project_id, db)
    task = prepare_assets_task.delay(video_project_id)
    return _dispatch_task(video_project_id, task.id, "素材準備")


@router.post("/video-projects/{video_project_id}/pipeline/synthesize-audio")
def pipeline_synthesize_audio(
    video_project_id: str, request: Request, db: DbSession, csrf_token: Annotated[str, Form()]
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    _require_video_project(video_project_id, db)
    task = synthesize_audio_task.delay(video_project_id)
    return _dispatch_task(video_project_id, task.id, "音声合成")


@router.post("/video-projects/{video_project_id}/pipeline/render")
def pipeline_render(
    video_project_id: str, request: Request, db: DbSession, csrf_token: Annotated[str, Form()]
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    _require_video_project(video_project_id, db)
    task = render_video_task.delay(video_project_id)
    return _dispatch_task(video_project_id, task.id, "レンダリング")


@router.post("/video-projects/{video_project_id}/pipeline/review")
def pipeline_review(
    video_project_id: str, request: Request, db: DbSession, csrf_token: Annotated[str, Form()]
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    _require_video_project(video_project_id, db)
    task = run_automated_review_task.delay(video_project_id)
    return _dispatch_task(video_project_id, task.id, "自動レビュー")


@router.post("/video-projects/{video_project_id}/pipeline/upload")
def pipeline_upload(
    video_project_id: str,
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
    operator: Annotated[str, Depends(require_admin)],
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    _require_video_project(video_project_id, db)
    task = upload_video_task.delay(video_project_id)
    logger.info("pipeline_upload_dispatched", video_project_id=video_project_id, operator=operator)
    return _dispatch_task(video_project_id, task.id, "アップロード")


_THUMBNAIL_FILENAMES = {
    f"candidate_{i}.png" for i in range(thumbnails.THUMBNAIL_CANDIDATE_COUNT)
} | {"selected.png"}


@router.post("/video-projects/{video_project_id}/thumbnails/generate")
def generate_thumbnails_route(
    video_project_id: str,
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
) -> RedirectResponse:
    """サムネイル候補を(再)生成する。

    通常は素材準備ステップで自動生成されるが、サムネイル機能導入前に素材準備を
    済ませた既存プロジェクト(prepare_assetsのJobRunが成功済みでスキップされる)でも
    後から生成できるようにする。生成自体は決定的・冪等(spec_version一致なら再利用)。
    """
    require_csrf(request, csrf_token)
    _require_video_project(video_project_id, db)
    try:
        thumbnails.generate_thumbnail_candidates(db, video_project_id=video_project_id)
    except Exception as exc:  # noqa: BLE001 - Script未紐付け等をユーザー向けに表示する(500にしない)
        db.rollback()
        return _redirect_back(video_project_id, error=str(exc))
    db.commit()
    return _redirect_back(video_project_id, info="サムネイル候補を生成しました")


@router.post("/video-projects/{video_project_id}/thumbnail/select")
def select_thumbnail_route(
    video_project_id: str,
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
    candidate_index: Annotated[int, Form(ge=0, le=thumbnails.THUMBNAIL_CANDIDATE_COUNT - 1)],
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    _require_video_project(video_project_id, db)
    try:
        thumbnails.select_thumbnail(
            db, video_project_id=video_project_id, candidate_index=candidate_index
        )
    except thumbnails.ThumbnailNotFoundError as exc:
        db.rollback()
        return _redirect_back(video_project_id, error=str(exc))
    db.commit()
    return _redirect_back(video_project_id, info="サムネイルを選択しました")


@router.get("/media/{video_project_id}/thumbnails/{filename}")
def serve_thumbnail(video_project_id: str, filename: str, db: DbSession) -> FileResponse:
    """`generated/videos/{id}/thumbnails/` 配下のPNGをホワイトリスト付きで配信する。"""
    project = db.get(VideoProject, video_project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="video project not found")
    if filename not in _THUMBNAIL_FILENAMES:
        raise HTTPException(status_code=404, detail="thumbnail not found")

    try:
        resolved = resolve_generated_path(
            thumbnails.thumbnail_relative_path(video_project_id, filename)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not resolved.exists():
        raise HTTPException(status_code=404, detail="thumbnail file not found")

    return FileResponse(str(resolved), media_type="image/png")


@router.get("/media/{video_project_id}")
def serve_media(video_project_id: str, db: DbSession) -> FileResponse:
    """`generated/` 配下の動画をFileResponseで配信する(D-008: 境界検証必須)。"""
    project = db.get(VideoProject, video_project_id)
    if project is None or not project.output_path:
        raise HTTPException(status_code=404, detail="video not found")

    settings = get_settings()
    base_dir = Path(settings.GENERATED_DIR).resolve()
    output_path = Path(project.output_path)
    try:
        relative = output_path.resolve().relative_to(base_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid media path") from exc

    try:
        resolved = resolve_generated_path(relative)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not resolved.exists():
        raise HTTPException(status_code=404, detail="video file not found")

    return FileResponse(str(resolved), media_type="video/mp4")
