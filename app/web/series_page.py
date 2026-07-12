"""シリーズ企画・カリキュラム管理画面。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.csrf import get_or_issue_csrf_token, set_csrf_cookie
from app.db.session import get_db
from app.models.channel import Channel
from app.models.episode_plan import EpisodePlan
from app.models.series_plan import SeriesPlan
from app.models.video_project import VideoProject
from app.services.orchestration import _advance_status, _ensure_dummy_evidence
from app.services.series.service import approve_curriculum, create_topic_from_episode
from app.web.common import require_csrf, with_message
from app.workers.tasks.scripts import generate_script_task
from app.workers.tasks.series import generate_curriculum_task

router = APIRouter(tags=["web-series"])
DbSession = Annotated[Session, Depends(get_db)]


def _series_or_404(db: Session, series_id: str) -> SeriesPlan:
    series = db.get(SeriesPlan, series_id)
    if series is None:
        raise HTTPException(status_code=404, detail="SeriesPlan not found")
    return series


@router.get("/series", response_class=HTMLResponse)
def list_series(request: Request, db: DbSession) -> HTMLResponse:
    series_list = db.query(SeriesPlan).order_by(SeriesPlan.updated_at.desc()).all()
    channels = db.query(Channel).order_by(Channel.name).all()
    episode_counts = {
        series.id: db.query(EpisodePlan).filter(EpisodePlan.series_plan_id == series.id).count()
        for series in series_list
    }
    csrf_token = get_or_issue_csrf_token(request)
    response = request.app.state.templates.TemplateResponse(
        request,
        "series/list.html",
        {
            "series_list": series_list,
            "channels": channels,
            "episode_counts": episode_counts,
            "csrf_token": csrf_token,
        },
    )
    set_csrf_cookie(response, csrf_token)
    return response


@router.post("/series")
def create_series(
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
    channel_id: Annotated[str, Form()],
    name: Annotated[str, Form(min_length=1, max_length=255)],
    target_audience: Annotated[str, Form(min_length=1, max_length=5_000)],
    starting_knowledge: Annotated[str, Form(min_length=1, max_length=5_000)],
    final_goal: Annotated[str, Form(min_length=1, max_length=5_000)],
    planned_episode_count: Annotated[int, Form(ge=2, le=100)],
    series_prompt: Annotated[str, Form(max_length=10_000)] = "",
    shared_rules: Annotated[str, Form(max_length=10_000)] = "",
    technology_version: Annotated[str, Form(max_length=128)] = "Python 3.12",
    development_environment: Annotated[str, Form(max_length=255)] = "VS Code",
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    if db.get(Channel, channel_id) is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    series = SeriesPlan(
        channel_id=channel_id,
        name=name.strip(),
        target_audience=target_audience.strip(),
        starting_knowledge=starting_knowledge.strip(),
        final_goal=final_goal.strip(),
        planned_episode_count=planned_episode_count,
        series_prompt=series_prompt.strip(),
        shared_rules=shared_rules.strip(),
        technology_version=technology_version.strip(),
        development_environment=development_environment.strip(),
    )
    try:
        db.add(series)
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(
            url=with_message("/series", error="同じチャンネルに同名シリーズがあります"),
            status_code=303,
        )
    return RedirectResponse(url=f"/series/{series.id}", status_code=303)


@router.get("/series/{series_id}", response_class=HTMLResponse)
def series_detail(
    series_id: str,
    request: Request,
    db: DbSession,
    task_id: str | None = None,
    task_label: str | None = None,
) -> HTMLResponse:
    series = _series_or_404(db, series_id)
    episodes = (
        db.query(EpisodePlan)
        .filter(EpisodePlan.series_plan_id == series.id)
        .order_by(EpisodePlan.position)
        .all()
    )
    csrf_token = get_or_issue_csrf_token(request)
    response = request.app.state.templates.TemplateResponse(
        request,
        "series/detail.html",
        {
            "series": series,
            "episodes": episodes,
            "csrf_token": csrf_token,
            "task_id": task_id,
            "task_label": task_label,
        },
    )
    set_csrf_cookie(response, csrf_token)
    return response


@router.post("/series/{series_id}/generate")
def generate_series_curriculum(
    series_id: str, request: Request, db: DbSession, csrf_token: Annotated[str, Form()]
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    series = _series_or_404(db, series_id)
    existing = db.query(EpisodePlan).filter(EpisodePlan.series_plan_id == series.id).all()
    if any(episode.topic_id for episode in existing):
        return RedirectResponse(
            url=with_message(
                f"/series/{series_id}", error="制作開始済みのため全体再生成できません"
            ),
            status_code=303,
        )
    if existing:
        series.curriculum_version += 1
        series.status = "draft"
        db.commit()
    task = generate_curriculum_task.delay(series_id)
    return RedirectResponse(
        url=f"/series/{series_id}?task_id={task.id}&task_label=カリキュラム生成",
        status_code=303,
    )


@router.post("/series/{series_id}/approve")
def approve_series_curriculum(
    series_id: str, request: Request, db: DbSession, csrf_token: Annotated[str, Form()]
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    series = _series_or_404(db, series_id)
    try:
        approve_curriculum(db, series)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            url=with_message(f"/series/{series_id}", error=str(exc)), status_code=303
        )
    return RedirectResponse(
        url=with_message(f"/series/{series_id}", info="カリキュラムを承認しました"),
        status_code=303,
    )


def _csv_values(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@router.post("/series/{series_id}/episodes/{episode_id}/update")
def update_episode(
    series_id: str,
    episode_id: str,
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
    title: Annotated[str, Form(min_length=1, max_length=255)],
    summary: Annotated[str, Form(min_length=1, max_length=5_000)],
    learning_objectives: Annotated[str, Form(max_length=5_000)],
    new_concepts: Annotated[str, Form(max_length=5_000)],
    review_concepts: Annotated[str, Form(max_length=5_000)],
    excluded_concepts: Annotated[str, Form(max_length=5_000)],
    demo_outline: Annotated[str, Form(max_length=5_000)],
    exercise_outline: Annotated[str, Form(max_length=5_000)],
    next_episode_bridge: Annotated[str, Form(max_length=2_000)],
    target_duration_seconds: Annotated[int, Form(ge=30, le=3600)],
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    series = _series_or_404(db, series_id)
    episode = db.get(EpisodePlan, episode_id)
    if episode is None or episode.series_plan_id != series.id:
        raise HTTPException(status_code=404, detail="EpisodePlan not found")
    if episode.topic_id:
        return RedirectResponse(
            url=with_message(f"/series/{series_id}", error="制作開始済みのEpisodeは編集できません"),
            status_code=303,
        )
    episode.title = title.strip()
    episode.summary = summary.strip()
    episode.learning_objectives = _csv_values(learning_objectives)
    episode.new_concepts = _csv_values(new_concepts)
    episode.review_concepts = _csv_values(review_concepts)
    episode.excluded_concepts = _csv_values(excluded_concepts)
    episode.demo_outline = demo_outline.strip()
    episode.exercise_outline = exercise_outline.strip()
    episode.next_episode_bridge = next_episode_bridge.strip()
    episode.target_duration_seconds = target_duration_seconds
    if not episode.learning_objectives:
        return RedirectResponse(
            url=with_message(f"/series/{series_id}", error="学習目標を1つ以上指定してください"),
            status_code=303,
        )
    if series.status == "approved":
        series.status = "curriculum_draft"
        for item in db.query(EpisodePlan).filter(EpisodePlan.series_plan_id == series.id):
            if not item.topic_id:
                item.status = "draft"
    db.commit()
    return RedirectResponse(
        url=with_message(f"/series/{series_id}", info="Episodeを更新しました"), status_code=303
    )


@router.post("/series/{series_id}/episodes/{episode_id}/move")
def move_episode(
    series_id: str,
    episode_id: str,
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
    direction: Annotated[str, Form()],
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    series = _series_or_404(db, series_id)
    episode = db.get(EpisodePlan, episode_id)
    if episode is None or episode.series_plan_id != series.id or episode.topic_id:
        raise HTTPException(status_code=409, detail="Episodeを移動できません")
    target_position = episode.position - 1 if direction == "up" else episode.position + 1
    target = (
        db.query(EpisodePlan)
        .filter(
            EpisodePlan.series_plan_id == series.id,
            EpisodePlan.position == target_position,
        )
        .one_or_none()
    )
    if target is None or target.topic_id:
        return RedirectResponse(
            url=with_message(f"/series/{series_id}", error="これ以上移動できません"),
            status_code=303,
        )
    original_position = episode.position
    episode.position = 0
    db.flush()
    target.position = original_position
    db.flush()
    episode.position = target_position
    series.status = "curriculum_draft"
    db.commit()
    return RedirectResponse(url=f"/series/{series_id}", status_code=303)


@router.post("/series/{series_id}/episodes/{episode_id}/start")
def start_episode_production(
    series_id: str,
    episode_id: str,
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    series = _series_or_404(db, series_id)
    episode = db.get(EpisodePlan, episode_id)
    if episode is None or episode.series_plan_id != series.id:
        raise HTTPException(status_code=404, detail="EpisodePlan not found")
    try:
        topic, project_id = create_topic_from_episode(db, series=series, episode=episode)
        project = db.get(VideoProject, project_id)
        if project is None:
            raise RuntimeError("VideoProject not found after creation")
        _advance_status(project, "TOPIC_SCORED")
        _ensure_dummy_evidence(db, topic_id=topic.id)
        _advance_status(project, "RESEARCH_READY")
        db.commit()
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            url=with_message(f"/series/{series_id}", error=str(exc)), status_code=303
        )
    task = generate_script_task.delay(topic.id)
    return RedirectResponse(
        url=f"/video-projects/{project_id}?task_id={task.id}&task_label=シリーズ台本生成",
        status_code=303,
    )
