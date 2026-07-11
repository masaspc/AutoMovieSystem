"""動画プロジェクト一覧・詳細・パイプライン進行ボタン・動画プレビュー配信。"""

from __future__ import annotations

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
from app.services.media.dialogue import dialogue_script_enabled, extract_speech_lines
from app.web.common import require_csrf, with_message
from app.workers.tasks.media import (
    prepare_assets_task,
    render_video_task,
    synthesize_audio_task,
)
from app.workers.tasks.publishing import upload_video_task
from app.workers.tasks.reviews import run_automated_review_task

logger = get_logger(__name__)

router = APIRouter(tags=["web-video-projects"])

DbSession = Annotated[Session, Depends(get_db)]


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
            "reviews": reviews,
            "blocking_findings": blocking_findings,
            "approval": approval,
            "publication": publication,
            "metrics": metrics,
            "comments": comments,
            "insights": insights,
            "media_available": media_available,
            "next_action": next_action,
            "dialogue_script_enabled": dialogue_script_enabled(),
            "csrf_token": csrf_token,
            "task_id": task_id,
            "task_label": task_label,
        },
    )
    set_csrf_cookie(response, csrf_token)
    return response


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
    logger.info(
        "pipeline_upload_dispatched", video_project_id=video_project_id, operator=operator
    )
    return _dispatch_task(video_project_id, task.id, "アップロード")


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
