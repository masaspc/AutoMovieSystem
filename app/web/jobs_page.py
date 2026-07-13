"""JobRun一覧+失敗ジョブ再実行ページ(仕様§16)。"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.csrf import get_or_issue_csrf_token, set_csrf_cookie
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.job_run import JobRun
from app.providers.llm.factory import get_llm_provider
from app.providers.tts.factory import get_tts_provider
from app.providers.youtube.factory import get_youtube_provider
from app.services.feedback.self_review import run_self_review
from app.services.jobs import JobInProgressError
from app.services.media.pipeline import (
    prepare_assets,
    render_video,
    restart_render,
    synthesize_audio,
)
from app.services.publishing.uploader import restart_upload, upload_video
from app.services.reviews.service import restart_review, run_automated_review
from app.services.scripts.generator import generate_script
from app.services.topics.scoring import score_topic
from app.web.common import require_csrf, with_message

logger = get_logger(__name__)

router = APIRouter(tags=["web-jobs"])

DbSession = Annotated[Session, Depends(get_db)]

# 復旧サービスを経由してから該当タスクを再実行できるjob_type。
_MANUAL_INTERVENTION_MESSAGE = "対応する自動再実行手順がないため手動対応が必要です"


class _ManualInterventionRequiredError(RuntimeError):
    """判断できないjob_typeに対する再実行が要求された場合。"""


async def _retry_job(session: Session, job_run: JobRun) -> None:
    job_type = job_run.job_type
    entity_id = job_run.entity_id

    if job_type == "prepare_assets":
        prepare_assets(session, video_project_id=entity_id)
    elif job_type == "synthesize_audio":
        await synthesize_audio(session, video_project_id=entity_id, provider=get_tts_provider())
    elif job_type == "render_video":
        restart_render(session, video_project_id=entity_id)
        session.flush()
        render_video(session, video_project_id=entity_id)
    elif job_type == "automated_review":
        restart_review(session, video_project_id=entity_id)
        session.flush()
        await run_automated_review(session, video_project_id=entity_id, provider=get_llm_provider())
    elif job_type == "upload_video":
        restart_upload(session, video_project_id=entity_id)
        session.flush()
        await upload_video(session, video_project_id=entity_id, provider=get_youtube_provider())
    elif job_type == "score_topic":
        score_topic(session, entity_id)
    elif job_type == "generate_script":
        await generate_script(session, topic_id=entity_id, provider=get_llm_provider())
    elif job_type == "self_review":
        try:
            metric_date = date.fromisoformat(job_run.idempotency_key.rsplit(":", 1)[-1])
        except ValueError:
            metric_date = None
        await run_self_review(
            session,
            publication_id=entity_id,
            provider=get_llm_provider(),
            metric_date=metric_date,
        )
    else:
        raise _ManualInterventionRequiredError(job_type)


@router.get("/jobs", response_class=HTMLResponse)
def list_jobs(
    request: Request,
    db: DbSession,
    status: str | None = None,
    job_type: str | None = None,
) -> HTMLResponse:
    query = db.query(JobRun)
    if status:
        query = query.filter(JobRun.status == status)
    if job_type:
        query = query.filter(JobRun.job_type == job_type)
    jobs = query.order_by(JobRun.created_at.desc()).limit(200).all()

    csrf_token = get_or_issue_csrf_token(request)
    templates = request.app.state.templates
    response = templates.TemplateResponse(
        request,
        "jobs/list.html",
        {
            "jobs": jobs,
            "status_filter": status or "",
            "job_type_filter": job_type or "",
            "csrf_token": csrf_token,
        },
    )
    set_csrf_cookie(response, csrf_token)
    return response


@router.post("/jobs/{job_run_id}/retry")
async def retry_job(
    job_run_id: str, request: Request, db: DbSession, csrf_token: Annotated[str, Form()]
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    job_run = db.get(JobRun, job_run_id)
    if job_run is None:
        raise HTTPException(status_code=404, detail=f"JobRun not found: {job_run_id}")

    try:
        await _retry_job(db, job_run)
    except _ManualInterventionRequiredError:
        db.rollback()
        return RedirectResponse(
            url=with_message("/jobs", info=_MANUAL_INTERVENTION_MESSAGE), status_code=303
        )
    except JobInProgressError:
        db.rollback()
        return RedirectResponse(url=with_message("/jobs", error="実行中です"), status_code=303)
    except Exception as exc:  # noqa: BLE001 - サービス例外をユーザー向けに表示する(500にしない)
        db.commit()
        logger.info("job_retry_failed", job_run_id=job_run_id, job_type=job_run.job_type)
        return RedirectResponse(url=with_message("/jobs", error=str(exc)), status_code=303)

    db.commit()
    logger.info("job_retry_succeeded", job_run_id=job_run_id, job_type=job_run.job_type)
    return RedirectResponse(url="/jobs", status_code=303)
