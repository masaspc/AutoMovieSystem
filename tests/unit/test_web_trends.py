"""トレンド一覧と即動画化Webフローの検証。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.topic import Topic
from app.providers.trends.fake import FakeTrendProvider


@dataclass
class _FakeAsyncResult:
    id: str


def _make_channel(db_session: Session, name: str) -> Channel:
    channel = Channel(name=name)
    db_session.add(channel)
    db_session.flush()
    return channel


def test_trends_page_lists_fake_items_and_channel_choices(
    client: TestClient, db_session: Session
) -> None:
    _make_channel(db_session, "チャンネルA")
    _make_channel(db_session, "チャンネルB")
    db_session.commit()

    response = client.get("/trends")

    assert response.status_code == 200
    assert "新しい生成AIモデルが発表される" in response.text
    assert "即動画化" in response.text
    assert "チャンネルA" in response.text
    assert "チャンネルB" in response.text


def test_trends_page_without_channel_shows_guidance(client: TestClient) -> None:
    response = client.get("/trends")

    assert response.status_code == 200
    assert "動画化先のチャンネルがありません" in response.text
    assert "disabled" in response.text


def test_trends_page_resolves_feed_for_selected_channel(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _make_channel(db_session, "全体設定")
    selected = _make_channel(db_session, "金融")
    selected.editorial_policy = {"trend_feed_urls": ["https://finance.example/rss"]}
    db_session.commit()
    captured: list[str] = []

    def fake_factory(settings):  # type: ignore[no-untyped-def]
        captured.append(settings.TREND_FEED_URLS)
        return FakeTrendProvider()

    monkeypatch.setattr("app.web.trends_page.get_trend_provider", fake_factory)
    response = client.get(f"/trends?channel_id={selected.id}")
    assert response.status_code == 200
    assert captured == ["https://finance.example/rss"]
    assert f'value="{selected.id}" selected' in response.text
    assert first.id != selected.id


def test_videoize_uses_selected_channel_and_dispatches_production(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _make_channel(db_session, "チャンネルA")
    selected = _make_channel(db_session, "チャンネルB")
    db_session.commit()
    dispatched: list[str] = []

    def fake_delay(topic_id: str) -> _FakeAsyncResult:
        dispatched.append(topic_id)
        return _FakeAsyncResult(id="task-trend-1")

    monkeypatch.setattr("app.web.trends_page.produce_video_task.delay", fake_delay)
    csrf_token = client.get("/trends").cookies["csrf_token"]

    response = client.post(
        "/trends/videoize",
        data={
            "csrf_token": csrf_token,
            "channel_id": selected.id,
            "title": "新モデル発表",
            "url": "https://example.com/news/new-model",
            "summary": "新モデルの短い要約",
            "source": "Example News",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    topic = db_session.query(Topic).filter(Topic.source_type == "trend").one()
    assert topic.channel_id == selected.id
    assert topic.channel_id != first.id
    assert dispatched == [topic.id]
    location = urlsplit(response.headers["location"])
    assert location.path == f"/topics/{topic.id}"
    query = parse_qs(location.query)
    assert query["task_id"] == ["task-trend-1"]
    assert query["task_label"] == ["トレンド一括制作"]


def test_videoize_rejects_unknown_channel_without_dispatch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def fake_delay(_topic_id: str) -> _FakeAsyncResult:
        nonlocal called
        called = True
        return _FakeAsyncResult(id="unused")

    monkeypatch.setattr("app.web.trends_page.produce_video_task.delay", fake_delay)
    csrf_token = client.get("/trends").cookies["csrf_token"]

    response = client.post(
        "/trends/videoize",
        data={
            "csrf_token": csrf_token,
            "channel_id": "missing",
            "title": "記事",
            "url": "https://example.com/article",
            "summary": "要約",
            "source": "News",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert called is False


def test_videoize_requires_valid_csrf(client: TestClient, db_session: Session) -> None:
    channel = _make_channel(db_session, "チャンネル")
    db_session.commit()

    response = client.post(
        "/trends/videoize",
        data={
            "csrf_token": "invalid",
            "channel_id": channel.id,
            "title": "記事",
            "url": "https://example.com/article",
            "summary": "要約",
            "source": "News",
        },
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert db_session.query(Topic).filter(Topic.source_type == "trend").count() == 0
