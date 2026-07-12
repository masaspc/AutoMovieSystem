from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.episode_plan import EpisodePlan
from app.models.series_plan import SeriesPlan
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.providers.llm.fake import DeterministicFakeLLMProvider
from app.services.series.context import build_series_script_context
from app.services.series.service import (
    approve_curriculum,
    create_topic_from_episode,
    generate_curriculum,
)


@dataclass
class _TaskResult:
    id: str = "series-task"


def _channel(session: Session) -> Channel:
    channel = Channel(name="series-channel")
    session.add(channel)
    session.commit()
    return channel


def _series(session: Session, *, count: int = 3) -> SeriesPlan:
    channel = _channel(session)
    series = SeriesPlan(
        channel_id=channel.id,
        name="Python初心者講座",
        target_audience="プログラミング未経験者",
        starting_knowledge="PCの基本操作",
        final_goal="簡単なCLIアプリを作る",
        series_prompt="新概念は1回3個まで",
        shared_rules="未説明構文を使わない",
        planned_episode_count=count,
    )
    session.add(series)
    session.commit()
    return series


def test_generate_curriculum_approve_and_start_episode(db_session: Session) -> None:
    series = _series(db_session)
    episodes = asyncio.run(
        generate_curriculum(
            db_session,
            series_id=series.id,
            provider=DeterministicFakeLLMProvider(),
        )
    )
    assert [episode.position for episode in episodes] == [1, 2, 3]
    assert episodes[1].prerequisite_positions == [1]

    approve_curriculum(db_session, series)
    topic, project_id = create_topic_from_episode(
        db_session, series=series, episode=episodes[0]
    )
    db_session.commit()

    assert topic.source_ref.startswith(f"series:{series.id}:episode:")
    project = db_session.get(VideoProject, project_id)
    assert project is not None
    assert project.production_settings["preset"] == "custom"
    assert project.production_settings["target_duration_seconds"] == 300
    context = build_series_script_context(db_session, topic.id)
    assert "Python初心者講座" in context
    assert "まだ説明・使用してはいけない概念" in context


def test_curriculum_regeneration_is_blocked_after_topic_creation(db_session: Session) -> None:
    series = _series(db_session, count=2)
    episodes = asyncio.run(
        generate_curriculum(
            db_session,
            series_id=series.id,
            provider=DeterministicFakeLLMProvider(),
        )
    )
    approve_curriculum(db_session, series)
    create_topic_from_episode(db_session, series=series, episode=episodes[0])
    with pytest.raises(ValueError, match="制作開始済み"):
        asyncio.run(
            generate_curriculum(
                db_session,
                series_id=series.id,
                provider=DeterministicFakeLLMProvider(),
            )
        )


def test_series_web_create_and_generate_dispatch(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = _channel(db_session)
    page = client.get("/series")
    assert page.status_code == 200
    assert "シリーズ" in page.text
    token = page.cookies["csrf_token"]
    response = client.post(
        "/series",
        data={
            "csrf_token": token,
            "channel_id": channel.id,
            "name": "Web講座",
            "target_audience": "初心者",
            "starting_knowledge": "なし",
            "final_goal": "完成",
            "planned_episode_count": "4",
            "series_prompt": "順番に教える",
            "shared_rules": "飛ばさない",
            "technology_version": "Python 3.12",
            "development_environment": "VS Code",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    series = db_session.query(SeriesPlan).filter(SeriesPlan.name == "Web講座").one()

    captured: list[str] = []
    monkeypatch.setattr(
        "app.web.series_page.generate_curriculum_task.delay",
        lambda series_id: captured.append(series_id) or _TaskResult(),
    )
    detail = client.get(f"/series/{series.id}")
    token = detail.cookies["csrf_token"]
    response = client.post(
        f"/series/{series.id}/generate",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert captured == [series.id]


def test_episode_topic_is_unique_on_repeated_start(db_session: Session) -> None:
    series = _series(db_session, count=2)
    episodes = asyncio.run(
        generate_curriculum(
            db_session,
            series_id=series.id,
            provider=DeterministicFakeLLMProvider(),
        )
    )
    approve_curriculum(db_session, series)
    first_topic, first_project = create_topic_from_episode(
        db_session, series=series, episode=episodes[0]
    )
    second_topic, second_project = create_topic_from_episode(
        db_session, series=series, episode=episodes[0]
    )
    assert first_topic.id == second_topic.id
    assert first_project == second_project
    assert db_session.query(Topic).count() == 1
    assert db_session.query(EpisodePlan).count() == 2


def test_episode_prerequisites_must_be_started_in_order(db_session: Session) -> None:
    series = _series(db_session, count=2)
    episodes = asyncio.run(
        generate_curriculum(
            db_session,
            series_id=series.id,
            provider=DeterministicFakeLLMProvider(),
        )
    )
    approve_curriculum(db_session, series)
    with pytest.raises(ValueError, match="前提Episode"):
        create_topic_from_episode(db_session, series=series, episode=episodes[1])
