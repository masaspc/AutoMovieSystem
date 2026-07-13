"""セルフレビューCeleryタスクの対象日・fail-soft・失敗永続化。"""

from __future__ import annotations

from collections.abc import Generator
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.db.session as db_session_module
import app.workers.tasks.feedback as feedback_tasks
from app.db.base import Base
from app.db.session import enable_sqlite_foreign_keys
from app.models.channel import Channel
from app.models.job_run import JobRun
from app.models.publication import Publication
from app.models.topic import Topic
from app.models.video_metric_daily import VideoMetricDaily
from app.models.video_project import VideoProject
from app.workers.celery_app import celery_app


@pytest.fixture
def task_session_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    db_path = tmp_path / "feedback_tasks.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(feedback_tasks, "SessionLocal", factory)
    monkeypatch.setattr(db_session_module, "SessionLocal", factory)
    try:
        yield factory
    finally:
        engine.dispose()


def _publication_with_metric(
    session: Session,
    *,
    source_ref: str,
    metric_date: date,
) -> Publication:
    channel = Channel(name=f"channel-{source_ref}")
    session.add(channel)
    session.flush()
    topic = Topic(
        channel_id=channel.id,
        title=f"topic-{source_ref}",
        source_type="manual",
        source_ref=source_ref,
    )
    session.add(topic)
    session.flush()
    project = VideoProject(topic_id=topic.id, status="UPLOADED_PRIVATE", generation=1)
    session.add(project)
    session.flush()
    publication = Publication(
        video_project_id=project.id,
        youtube_video_id=f"video-{source_ref}"[:32],
        title=f"video-{source_ref}",
        description="",
        privacy_status="private",
        idempotency_key=f"upload:{source_ref}",
        upload_status="completed",
    )
    session.add(publication)
    session.flush()
    session.add(
        VideoMetricDaily(
            publication_id=publication.id,
            metric_date=metric_date,
            views=100,
            impressions=1000,
            ctr=0.05,
            average_view_percentage=0.4,
        )
    )
    session.flush()
    return publication


def test_manual_self_review_task_returns_insight_count(
    task_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = object()
    calls: list[str] = []

    async def _fake_run(
        session: Session,
        *,
        publication_id: str,
        provider: object,
        metric_date: date | None = None,
    ) -> list[object]:
        del session, provider, metric_date
        calls.append(publication_id)
        return [object(), object()]

    monkeypatch.setattr(feedback_tasks, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(feedback_tasks, "run_self_review", _fake_run)

    assert feedback_tasks.run_self_review_task("publication-1") == 2
    assert calls == ["publication-1"]


def test_manual_self_review_task_persists_job_failure_after_rollback(
    task_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fail_run(*args: object, **kwargs: object) -> list[object]:
        del args, kwargs
        exc = RuntimeError("simulated self review failure")
        exc.idempotency_key = "self_review:publication-failed:2026-07-12"  # type: ignore[attr-defined]
        raise exc

    monkeypatch.setattr(feedback_tasks, "get_llm_provider", object)
    monkeypatch.setattr(feedback_tasks, "run_self_review", _fail_run)

    with pytest.raises(RuntimeError, match="simulated self review failure"):
        feedback_tasks.run_self_review_task("publication-failed")

    verify_session = task_session_factory()
    try:
        job_run = (
            verify_session.query(JobRun)
            .filter(JobRun.idempotency_key == "self_review:publication-failed:2026-07-12")
            .one()
        )
        assert job_run.status == "failed"
        assert job_run.job_type == "self_review"
        assert job_run.entity_id == "publication-failed"
        assert "simulated self review failure" in (job_run.last_error or "")
    finally:
        verify_session.close()


def test_daily_self_reviews_use_previous_day_and_continue_after_failure(
    task_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = datetime(2026, 7, 13, 12, 0, 0)
    previous_day = date(2026, 7, 12)
    setup_session = task_session_factory()
    first = _publication_with_metric(
        setup_session,
        source_ref="daily-success",
        metric_date=previous_day,
    )
    failing = _publication_with_metric(
        setup_session,
        source_ref="daily-failure",
        metric_date=previous_day,
    )
    today_only = _publication_with_metric(
        setup_session,
        source_ref="daily-today-only",
        metric_date=fixed_now.date(),
    )
    first_id = first.id
    failing_id = failing.id
    today_only_id = today_only.id
    setup_session.commit()
    setup_session.close()

    calls: list[tuple[str, date | None]] = []

    async def _fake_run(
        session: Session,
        *,
        publication_id: str,
        provider: object,
        metric_date: date | None = None,
    ) -> list[object]:
        del session, provider
        calls.append((publication_id, metric_date))
        if publication_id == failing_id:
            exc = RuntimeError("one publication failed")
            exc.idempotency_key = (  # type: ignore[attr-defined]
                f"self_review:{publication_id}:{previous_day.isoformat()}"
            )
            raise exc
        return [object(), object()]

    monkeypatch.setattr(feedback_tasks, "utcnow_naive", lambda: fixed_now)
    monkeypatch.setattr(feedback_tasks, "get_llm_provider", object)
    monkeypatch.setattr(feedback_tasks, "run_self_review", _fake_run)

    summary = feedback_tasks.run_daily_self_reviews()

    assert summary == {
        "metric_date": previous_day.isoformat(),
        "candidate_publications": 2,
        "successful_attempts": 1,
        "failed_attempts": 1,
        "insights_returned": 2,
    }
    assert {publication_id for publication_id, _ in calls} == {first_id, failing_id}
    assert today_only_id not in {publication_id for publication_id, _ in calls}
    assert all(metric_date == previous_day for _, metric_date in calls)

    verify_session = task_session_factory()
    try:
        failed_job = (
            verify_session.query(JobRun)
            .filter(JobRun.entity_id == failing_id, JobRun.job_type == "self_review")
            .one()
        )
        assert failed_job.status == "failed"
    finally:
        verify_session.close()


def test_daily_self_reviews_continue_when_provider_initialization_fails_once(
    task_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = datetime(2026, 7, 13, 12, 0, 0)
    previous_day = date(2026, 7, 12)
    setup_session = task_session_factory()
    _publication_with_metric(setup_session, source_ref="provider-a", metric_date=previous_day)
    _publication_with_metric(setup_session, source_ref="provider-b", metric_date=previous_day)
    setup_session.commit()
    setup_session.close()
    provider_calls = 0
    review_calls: list[str] = []

    def _flaky_provider_factory() -> object:
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            raise RuntimeError("provider is temporarily unavailable")
        return object()

    async def _fake_run(
        session: Session,
        *,
        publication_id: str,
        provider: object,
        metric_date: date | None = None,
    ) -> list[object]:
        del session, provider, metric_date
        review_calls.append(publication_id)
        return []

    monkeypatch.setattr(feedback_tasks, "utcnow_naive", lambda: fixed_now)
    monkeypatch.setattr(feedback_tasks, "get_llm_provider", _flaky_provider_factory)
    monkeypatch.setattr(feedback_tasks, "run_self_review", _fake_run)

    summary = feedback_tasks.run_daily_self_reviews()

    assert summary["candidate_publications"] == 2
    assert summary["successful_attempts"] == 1
    assert summary["failed_attempts"] == 1
    assert provider_calls == 2
    assert len(review_calls) == 1
    verify_session = task_session_factory()
    try:
        failed_job = verify_session.query(JobRun).filter(JobRun.status == "failed").one()
        assert failed_job.idempotency_key.endswith(":2026-07-12")
    finally:
        verify_session.close()


def test_feedback_tasks_are_registered_for_worker_and_daily_beat() -> None:
    assert "app.workers.tasks.feedback" in celery_app.conf.include
    entry = celery_app.conf.beat_schedule["run-daily-self-reviews"]
    assert entry["task"] == "feedback.run_daily_self_reviews"
    assert entry["schedule"] == 86400.0
