"""レビュー結果表示+承認/却下フォーム(HTMX管理画面)。CSRF対策必須。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.auth import require_admin
from app.core.csrf import CSRF_COOKIE_NAME, issue_csrf_token, set_csrf_cookie, verify_csrf
from app.db.session import get_db
from app.models.review import Review
from app.models.video_project import VideoProject
from app.services.reviews import approval
from app.services.state_machine import InvalidTransitionError

router = APIRouter(tags=["web-approvals"])

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
    csrf_token = issue_csrf_token()

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


def _handle_transition_errors(exc: Exception) -> None:
    if isinstance(exc, approval.VideoProjectNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, InvalidTransitionError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


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
    except (approval.VideoProjectNotFoundError, InvalidTransitionError) as exc:
        db.rollback()
        _handle_transition_errors(exc)
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
    except (approval.VideoProjectNotFoundError, InvalidTransitionError) as exc:
        db.rollback()
        _handle_transition_errors(exc)
    db.commit()
    return RedirectResponse(url=f"/video-projects/{video_project_id}/review", status_code=303)
