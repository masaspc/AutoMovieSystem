"""明示オプトインの日次グロース自動運転タスク。"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.workers.tasks.growth as growth_tasks
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import enable_sqlite_foreign_keys
from app.models.channel import Channel
from app.models.evidence import Evidence
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.providers.trends.base import TrendItem
from app.workers.celery_app import celery_app


@pytest.fixture
def task_session_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'growth_autopilot.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(growth_tasks, "SessionLocal", factory)
    try:
        yield factory
    finally:
        engine.dispose()


class _FakeTrendProvider:
    def __init__(self, items: list[TrendItem]) -> None:
        self.items = items
        self.requested_limits: list[int] = []

    async def fetch_latest(self, *, limit: int) -> list[TrendItem]:
        self.requested_limits.append(limit)
        return self.items[:limit]


def _item(index: int, *, url: str | None = None) -> TrendItem:
    return TrendItem(
        title=f"最新ニュース{index}",
        url=url or f"https://example.com/news/{index}",
        source="Test Feed",
        summary=f"ニュース{index}の短い要約",
        published_at=datetime(2026, 7, 14, index, tzinfo=UTC),
    )


def _enable(monkeypatch: pytest.MonkeyPatch, *, channel_id: str, daily_limit: int = 1) -> None:
    monkeypatch.setenv("GROWTH_AUTOPILOT_ENABLED", "true")
    monkeypatch.setenv("GROWTH_AUTOPILOT_CHANNEL_ID", channel_id)
    monkeypatch.setenv("GROWTH_AUTOPILOT_DAILY_LIMIT", str(daily_limit))
    get_settings.cache_clear()


def test_settings_reject_autopilot_limit_outside_one_to_three() -> None:
    with pytest.raises(ValidationError):
        Settings(GROWTH_AUTOPILOT_DAILY_LIMIT=0)
    with pytest.raises(ValidationError):
        Settings(GROWTH_AUTOPILOT_DAILY_LIMIT=4)


def test_disabled_autopilot_skips_without_fetching(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_AUTOPILOT_ENABLED", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(
        growth_tasks,
        "get_trend_provider",
        lambda settings: pytest.fail("disabled task must not initialize provider"),
    )

    result = growth_tasks.run_daily_autopilot()

    assert result["status"] == "skipped"
    assert result["reason"] == "disabled"


def test_enabled_autopilot_requires_existing_explicit_channel(
    task_session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch, channel_id="missing-channel")
    monkeypatch.setattr(
        growth_tasks,
        "get_trend_provider",
        lambda settings: pytest.fail("invalid channel must be rejected before fetch"),
    )

    result = growth_tasks.run_daily_autopilot()

    assert result["status"] == "skipped"
    assert result["reason"] == "channel_not_found"


def test_autopilot_creates_and_dispatches_up_to_daily_limit_fail_soft(
    task_session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = task_session_factory()
    channel = Channel(name="autopilot")
    setup.add(channel)
    setup.commit()
    channel_id = channel.id
    setup.close()
    _enable(monkeypatch, channel_id=channel_id, daily_limit=2)

    provider = _FakeTrendProvider(
        [_item(0, url="javascript:invalid"), _item(1), _item(2), _item(3)]
    )
    dispatched: list[str] = []
    monkeypatch.setattr(growth_tasks, "get_trend_provider", lambda settings: provider)
    monkeypatch.setattr(
        growth_tasks.produce_video_task,
        "delay",
        lambda topic_id: dispatched.append(topic_id),
    )

    result = growth_tasks.run_daily_autopilot()

    assert result["status"] == "completed_with_errors"
    assert result["selected"] == 2
    assert result["created"] == 2
    assert result["dispatched"] == 2
    assert result["failed"] == 1
    assert len(dispatched) == 2

    verify = task_session_factory()
    try:
        assert verify.query(Topic).count() == 2
        assert verify.query(Evidence).count() == 2
        projects = verify.query(VideoProject).all()
        assert len(projects) == 2
        assert all(project.status == "RESEARCH_READY" for project in projects)
    finally:
        verify.close()


def test_autopilot_url_idempotency_skips_finished_and_collects_next_item(
    task_session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = task_session_factory()
    channel = Channel(name="autopilot")
    setup.add(channel)
    setup.commit()
    channel_id = channel.id
    setup.close()
    _enable(monkeypatch, channel_id=channel_id)
    provider = _FakeTrendProvider([_item(1), _item(2)])
    dispatched: list[str] = []
    monkeypatch.setattr(growth_tasks, "get_trend_provider", lambda settings: provider)
    monkeypatch.setattr(
        growth_tasks.produce_video_task,
        "delay",
        lambda topic_id: dispatched.append(topic_id),
    )

    first = growth_tasks.run_daily_autopilot()
    assert first["created"] == 1

    update = task_session_factory()
    first_topic = update.query(Topic).filter(Topic.source_url.endswith("/1")).one()
    first_project = update.query(VideoProject).filter_by(topic_id=first_topic.id).one()
    first_project.status = "AUTOMATED_REVIEW_PASSED"
    update.commit()
    update.close()

    second = growth_tasks.run_daily_autopilot()

    assert second["created"] == 1
    assert second["already_finished"] == 1
    verify = task_session_factory()
    try:
        assert verify.query(Topic).count() == 2
        assert verify.query(Topic).filter(Topic.source_url.endswith("/1")).count() == 1
    finally:
        verify.close()


def test_growth_autopilot_task_registered_for_worker_and_daily_beat() -> None:
    assert "app.workers.tasks.growth" in celery_app.conf.include
    entry = celery_app.conf.beat_schedule["run-daily-growth-autopilot"]
    assert entry["task"] == "growth.run_daily_autopilot"
