"""成長ダッシュボード(グロース機能)。

登録者目標の進捗・投稿ペース・動画別パフォーマンスを表示し、
勝ち動画からの続編企画作成と量産バッチをここから実行する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.auth import require_admin
from app.core.config import get_settings
from app.core.csrf import get_or_issue_csrf_token, set_csrf_cookie
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.approval import Approval
from app.models.asset import Asset
from app.models.channel import Channel
from app.models.evidence import Evidence
from app.models.review import Review
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject
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
from app.services.reviews import approval as approval_service
from app.services.state_machine import InvalidTransitionError
from app.web.common import require_csrf, with_message
from app.workers.tasks.growth import run_daily_autopilot
from app.workers.tasks.publishing import upload_video_task

logger = get_logger(__name__)

router = APIRouter(tags=["web-growth"])

DbSession = Annotated[Session, Depends(get_db)]
AdminUser = Annotated[str, Depends(require_admin)]

_MAX_BATCH_LIMIT = 10


@dataclass(frozen=True)
class ApprovalQueueItem:
    """成長画面で1回の判断に必要な情報だけをまとめた表示モデル。"""

    project_id: str
    channel_name: str
    topic_title: str
    video_title: str
    thumbnail_url: str | None
    review_scores: tuple[tuple[str, float | None], ...]
    growth_readiness: str
    attention_summary: str
    evidence_links: tuple[tuple[str, str], ...]
    preview_url: str


def _latest_reviews(session: Session, video_project_id: str) -> list[Review]:
    reviews = (
        session.query(Review)
        .filter(Review.video_project_id == video_project_id)
        .order_by(Review.reviewer_type, Review.review_version.desc())
        .all()
    )
    seen: set[str] = set()
    result: list[Review] = []
    for review in reviews:
        if review.reviewer_type not in seen:
            seen.add(review.reviewer_type)
            result.append(review)
    return result


def _summarize_items(value: object) -> str:
    if not isinstance(value, list):
        return str(value).strip() if value is not None else ""
    labels: list[str] = []
    for item in value[:3]:
        if isinstance(item, dict):
            label = item.get("message") or item.get("summary") or item.get("code")
        else:
            label = item
        text = str(label or "").strip()
        if text:
            labels.append(text)
    return " / ".join(labels)


def _growth_quality_summary(source_manifest: object) -> tuple[str, str]:
    """新旧の品質メタデータを防御的に読み、欠損時は人間確認へ倒す。"""
    if not isinstance(source_manifest, dict):
        return "品質プリフライト未実施", "詳細確認が必要です"

    report = source_manifest.get("growth_quality_report")
    quick_ready = source_manifest.get("growth_quick_approval_ready")
    if isinstance(report, dict):
        score = report.get("overall_score")
        score_suffix = f" (score: {score})" if isinstance(score, int | float) else ""
        readiness = ("クイック承認可" if quick_ready is True else "要確認") + score_suffix
        attention = _summarize_items(report.get("human_check_reasons") or report.get("issues"))
        return readiness, attention or "注意事項はありません"

    # 移行中/拡張実装の `growth_quality` も、型を仮定せずに表示する。
    legacy = source_manifest.get("growth_quality")
    if isinstance(legacy, dict):
        ready = legacy.get("ready")
        readiness_value = legacy.get("readiness") or legacy.get("status")
        if readiness_value is None and isinstance(ready, bool):
            readiness_value = "クイック承認可" if ready else "要確認"
        score = legacy.get("overall_score")
        score_suffix = f" (score: {score})" if isinstance(score, int | float) else ""
        readiness = str(readiness_value or "要確認") + score_suffix
        attention = _summarize_items(
            legacy.get("attention_summary")
            or legacy.get("attention_items")
            or legacy.get("issues")
            or legacy.get("recommendations")
        )
        return readiness, attention or "詳細確認が必要です"

    return "品質プリフライト未実施", "詳細確認が必要です"


def _safe_evidence_links(evidence_list: list[Evidence]) -> tuple[tuple[str, str], ...]:
    links: list[tuple[str, str]] = []
    for evidence in evidence_list:
        parsed = urlsplit(evidence.source_url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            continue
        label = evidence.source_title or evidence.publisher or parsed.hostname
        links.append((str(label), evidence.source_url))
    return tuple(links)


def _approval_queue(session: Session, *, limit: int = 20) -> list[ApprovalQueueItem]:
    projects = (
        session.query(VideoProject)
        .filter(VideoProject.status == "AUTOMATED_REVIEW_PASSED")
        .order_by(VideoProject.updated_at.asc())
        .limit(limit)
        .all()
    )
    queue: list[ApprovalQueueItem] = []
    for project in projects:
        topic = session.get(Topic, project.topic_id)
        if topic is None:
            continue
        channel = session.get(Channel, topic.channel_id)
        script = session.get(Script, project.script_id) if project.script_id else None
        reviews = _latest_reviews(session, project.id)
        evidence_list = session.query(Evidence).filter(Evidence.topic_id == topic.id).all()
        selected_thumbnail = (
            session.query(Asset)
            .filter(Asset.video_project_id == project.id, Asset.role == "thumbnail")
            .one_or_none()
        )
        readiness, attention = _growth_quality_summary(
            script.source_manifest if script is not None else None
        )
        queue.append(
            ApprovalQueueItem(
                project_id=project.id,
                channel_name=channel.name if channel is not None else "不明なチャンネル",
                topic_title=topic.title,
                video_title=script.title if script is not None else topic.title,
                thumbnail_url=(
                    f"/media/{project.id}/thumbnails/selected.png"
                    if selected_thumbnail is not None
                    else None
                ),
                review_scores=tuple((review.reviewer_type, review.score) for review in reviews),
                growth_readiness=readiness,
                attention_summary=attention,
                evidence_links=_safe_evidence_links(evidence_list),
                preview_url=f"/media/{project.id}",
            )
        )
    return queue


@router.get("/growth", response_class=HTMLResponse)
def growth_dashboard(
    request: Request,
    db: DbSession,
    task_id: str | None = None,
    task_label: str | None = None,
) -> HTMLResponse:
    summary = compute_growth_summary(db)
    performances = list_video_performance(db, limit=20)
    channels = db.query(Channel).order_by(Channel.created_at.asc()).all()
    approval_queue = _approval_queue(db)
    settings = get_settings()
    configured_channel = (
        db.get(Channel, settings.GROWTH_AUTOPILOT_CHANNEL_ID.strip())
        if settings.GROWTH_AUTOPILOT_CHANNEL_ID.strip()
        else None
    )
    autopilot_ready = settings.GROWTH_AUTOPILOT_ENABLED and configured_channel is not None

    csrf_token = get_or_issue_csrf_token(request)
    templates = request.app.state.templates
    response = templates.TemplateResponse(
        request,
        "growth/index.html",
        {
            "summary": summary,
            "performances": performances,
            "channels": channels,
            "approval_queue": approval_queue,
            "autopilot_enabled": settings.GROWTH_AUTOPILOT_ENABLED,
            "autopilot_ready": autopilot_ready,
            "autopilot_channel": configured_channel,
            "autopilot_channel_id": settings.GROWTH_AUTOPILOT_CHANNEL_ID,
            "autopilot_daily_limit": settings.GROWTH_AUTOPILOT_DAILY_LIMIT,
            "autopilot_provider": settings.TREND_PROVIDER,
            "csrf_token": csrf_token,
            "task_id": task_id,
            "task_label": task_label,
        },
    )
    set_csrf_cookie(response, csrf_token)
    return response


@router.post("/growth/autopilot/run-now")
def run_autopilot_now(
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
) -> RedirectResponse:
    """設定済みの同じ日次タスクを手動dispatchする(DB処理はWebで実行しない)。"""
    require_csrf(request, csrf_token)
    settings = get_settings()
    channel_id = settings.GROWTH_AUTOPILOT_CHANNEL_ID.strip()
    configured = bool(
        settings.GROWTH_AUTOPILOT_ENABLED and channel_id and db.get(Channel, channel_id) is not None
    )
    if not configured:
        return RedirectResponse(
            url=with_message(
                "/growth", error="自動運転を有効化し、既存チャンネルIDを設定してください"
            ),
            status_code=303,
        )

    task = run_daily_autopilot.delay()
    return RedirectResponse(
        url=f"/growth?task_id={task.id}&task_label=グロース候補収集",
        status_code=303,
    )


@router.post("/growth/approval-queue/{video_project_id}/approve-private-upload")
def approve_and_upload_private(
    video_project_id: str,
    request: Request,
    db: DbSession,
    admin_user: AdminUser,
    csrf_token: Annotated[str, Form()],
) -> RedirectResponse:
    """承認を監査記録し、YouTube privateアップロードだけをdispatchする。"""
    require_csrf(request, csrf_token)
    settings = get_settings()
    if settings.YOUTUBE_DEFAULT_PRIVACY_STATUS != "private":
        return RedirectResponse(
            url=with_message(
                "/growth",
                error="安全のためYOUTUBE_DEFAULT_PRIVACY_STATUS=privateの場合だけ実行できます",
            ),
            status_code=303,
        )

    project = (
        db.query(VideoProject)
        .filter(VideoProject.id == video_project_id)
        .with_for_update()
        .one_or_none()
    )
    if project is None:
        raise HTTPException(status_code=404, detail="VideoProject not found")
    existing_approval = (
        db.query(Approval)
        .filter(
            Approval.video_project_id == video_project_id,
            Approval.decision == "approved",
        )
        .order_by(Approval.created_at.desc())
        .first()
    )

    if project.status == "AUTOMATED_REVIEW_PASSED" and existing_approval is None:
        try:
            approval_service.approve(
                db,
                video_project_id=video_project_id,
                decided_by=admin_user,
                reason="growth_dashboard_private_upload",
            )
        except InvalidTransitionError:
            db.rollback()
            return RedirectResponse(
                url=with_message("/growth", error="別の操作で処理済みです。画面を更新してください"),
                status_code=303,
            )
        db.commit()
    elif project.status == "UPLOAD_READY" and existing_approval is not None:
        # 承認commit後にdispatchだけ失敗したケースは、Approvalを増やさず再送できる。
        db.rollback()
    elif existing_approval is not None:
        db.rollback()
        return RedirectResponse(
            url=with_message("/growth", info="この動画は承認・アップロード処理済みです"),
            status_code=303,
        )
    else:
        db.rollback()
        return RedirectResponse(
            url=with_message("/growth", error="現在の状態では承認できません"),
            status_code=303,
        )

    try:
        task = upload_video_task.delay(video_project_id)
    except Exception as exc:  # noqa: BLE001 - 承認済み状態を保ち、詳細画面から再試行可能にする
        logger.warning(
            "growth_private_upload_dispatch_failed",
            video_project_id=video_project_id,
            operator=admin_user,
            error_type=type(exc).__name__,
        )
        return RedirectResponse(
            url=with_message(
                f"/video-projects/{video_project_id}",
                error="承認は記録しましたがアップロード開始に失敗しました。再実行してください",
            ),
            status_code=303,
        )

    logger.info(
        "growth_private_upload_dispatched",
        video_project_id=video_project_id,
        operator=admin_user,
    )
    return RedirectResponse(
        url=(
            f"/video-projects/{video_project_id}"
            f"?task_id={task.id}&task_label=YouTube非公開アップロード"
        ),
        status_code=303,
    )


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
