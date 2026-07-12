"""レビュー結果表示+承認/却下フォーム(HTMX管理画面)。CSRF対策必須。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.auth import require_admin
from app.core.csrf import CSRF_COOKIE_NAME, get_or_issue_csrf_token, set_csrf_cookie, verify_csrf
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.review import Review
from app.models.video_project import VideoProject
from app.services.reviews import approval
from app.services.state_machine import InvalidTransitionError
from app.web.common import with_message
from app.workers.tasks.scripts import generate_script_task

router = APIRouter(tags=["web-approvals"])
logger = get_logger(__name__)

DbSession = Annotated[Session, Depends(get_db)]
AdminUser = Annotated[str, Depends(require_admin)]


def _latest_reviews(session: Session, video_project_id: str) -> list[Review]:
    """reviewer_typeごとの最新(review_version最大)Reviewのみを返す。"""
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


@router.get("/video-projects/{video_project_id}/review", response_class=HTMLResponse)
def show_review(
    video_project_id: str, request: Request, db: DbSession, admin_user: AdminUser
) -> HTMLResponse:
    project = db.get(VideoProject, video_project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"VideoProject not found: {video_project_id}")

    reviews = _latest_reviews(db, video_project_id)
    csrf_token = get_or_issue_csrf_token(request)

    templates = request.app.state.templates
    response = templates.TemplateResponse(
        request,
        "reviews/review_detail.html",
        {
            "project": project,
            "reviews": reviews,
            "csrf_token": csrf_token,
            "operator": admin_user,
        },
    )
    set_csrf_cookie(response, csrf_token)
    return response


def _review_redirect(video_project_id: str, *, error: str | None = None) -> RedirectResponse:
    url = with_message(f"/video-projects/{video_project_id}/review", error=error)
    return RedirectResponse(url=url, status_code=303)


@router.post("/video-projects/{video_project_id}/approve")
def approve_video_project(
    video_project_id: str,
    request: Request,
    db: DbSession,
    admin_user: AdminUser,
    csrf_token: Annotated[str, Form()],
    reason: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not verify_csrf(cookie_token, csrf_token):
        raise HTTPException(status_code=403, detail="CSRF token invalid")

    try:
        approval.approve(
            db, video_project_id=video_project_id, decided_by=admin_user, reason=reason
        )
    except approval.VideoProjectNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidTransitionError:
        # 二重送信(2連続クリック等)で1回目が既に成立済みの場合にここへ来ることがある。
        # 生JSONを表示せず、レビュー画面へ戻して現在の状態をそのまま見せる。
        db.rollback()
        return _review_redirect(
            video_project_id, error="既に処理済み、または現在の状態では実行できません"
        )
    db.commit()
    return RedirectResponse(url=f"/video-projects/{video_project_id}/review", status_code=303)


@router.post("/video-projects/{video_project_id}/reject")
def reject_video_project(
    video_project_id: str,
    request: Request,
    db: DbSession,
    admin_user: AdminUser,
    csrf_token: Annotated[str, Form()],
    reason: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not verify_csrf(cookie_token, csrf_token):
        raise HTTPException(status_code=403, detail="CSRF token invalid")

    try:
        approval.reject(db, video_project_id=video_project_id, decided_by=admin_user, reason=reason)
    except approval.VideoProjectNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidTransitionError:
        db.rollback()
        return _review_redirect(
            video_project_id, error="既に処理済み、または現在の状態では実行できません"
        )
    db.commit()
    return RedirectResponse(url=f"/video-projects/{video_project_id}/review", status_code=303)


@router.post("/video-projects/{video_project_id}/rebuild-from-script")
def rebuild_video_project_from_script(
    video_project_id: str,
    request: Request,
    db: DbSession,
    admin_user: AdminUser,
    csrf_token: Annotated[str, Form()],
) -> RedirectResponse:
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not verify_csrf(cookie_token, csrf_token):
        raise HTTPException(status_code=403, detail="CSRF token invalid")
    try:
        replacement = approval.rebuild_from_script(db, video_project_id=video_project_id)
        replacement_id = replacement.id
        topic_id = replacement.topic_id
        db.commit()
    except approval.VideoProjectNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        return _review_redirect(video_project_id, error=str(exc))

    task = generate_script_task.delay(topic_id, replacement_id, replacement_id)
    logger.info(
        "video_project_rebuild_dispatched",
        source_video_project_id=video_project_id,
        replacement_video_project_id=replacement_id,
        operator=admin_user,
    )
    return RedirectResponse(
        url=(f"/video-projects/{replacement_id}?task_id={task.id}&task_label=台本から作り直し"),
        status_code=303,
    )
