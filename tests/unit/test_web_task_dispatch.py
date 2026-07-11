"""Web UIの各操作がCeleryタスクへdispatchし、進行状況ポーリング用のtask_idを
含めてリダイレクトすることを検証する(D-004: 長時間かかる処理をブロックしない)。

実際のタスク本体の正しさ(音声合成・レンダリング等)は各ドメインの既存テストで
担保済みのため、ここでは `.delay()` をモックしルーティング/リダイレクト形式のみを
検証する(Celeryタスクは `app.db.session.SessionLocal` という別セッションを使うため、
テストの `db_session` フィクスチャとは別DBになり、実行結果を直接検証できない)。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject


@dataclass
class _FakeAsyncResult:
    id: str


def _make_topic(db_session: Session) -> Topic:
    channel = Channel(name="ch")
    db_session.add(channel)
    db_session.flush()
    topic = Topic(channel_id=channel.id, title="topic", source_type="manual", source_ref="r-1")
    db_session.add(topic)
    db_session.commit()
    return topic


def _make_video_project(db_session: Session, topic: Topic) -> VideoProject:
    script = Script(
        topic_id=topic.id,
        version=1,
        title="タイトル",
        body={"sections": []},
        source_manifest={},
        status="reviewed",
    )
    db_session.add(script)
    db_session.flush()
    project = VideoProject(
        topic_id=topic.id, script_id=script.id, status="SCRIPT_REVIEWED", generation=1
    )
    db_session.add(project)
    db_session.commit()
    return project


def test_generate_script_dispatches_task_and_redirects_with_task_id(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    topic = _make_topic(db_session)
    monkeypatch.setattr(
        "app.web.topics.generate_script_task.delay",
        lambda topic_id: _FakeAsyncResult(id="task-123"),
    )

    get_response = client.get(f"/topics/{topic.id}")
    csrf_token = get_response.cookies["csrf_token"]

    post_response = client.post(
        f"/topics/{topic.id}/generate-script",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert post_response.status_code == 303
    location = urlsplit(post_response.headers["location"])
    assert location.path == f"/topics/{topic.id}"
    query = parse_qs(location.query)
    assert query["task_id"] == ["task-123"]
    assert query["task_label"] == ["台本生成"]


def test_generate_script_missing_topic_returns_404_without_dispatch(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def _delay(topic_id: str) -> _FakeAsyncResult:
        nonlocal called
        called = True
        return _FakeAsyncResult(id="unused")

    monkeypatch.setattr("app.web.topics.generate_script_task.delay", _delay)

    get_response = client.get("/topics")
    csrf_token = get_response.cookies["csrf_token"]

    response = client.post(
        "/topics/does-not-exist/generate-script",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert response.status_code == 404
    assert called is False


@pytest.mark.parametrize(
    ("action", "task_module_attr", "label"),
    [
        ("prepare-assets", "app.web.video_projects.prepare_assets_task.delay", "素材準備"),
        ("synthesize-audio", "app.web.video_projects.synthesize_audio_task.delay", "音声合成"),
        ("render", "app.web.video_projects.render_video_task.delay", "レンダリング"),
        ("review", "app.web.video_projects.run_automated_review_task.delay", "自動レビュー"),
        ("upload", "app.web.video_projects.upload_video_task.delay", "アップロード"),
    ],
)
def test_pipeline_action_dispatches_task_and_redirects_with_task_id(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    task_module_attr: str,
    label: str,
) -> None:
    topic = _make_topic(db_session)
    project = _make_video_project(db_session, topic)
    monkeypatch.setattr(task_module_attr, lambda video_project_id: _FakeAsyncResult(id="task-abc"))

    get_response = client.get(f"/video-projects/{project.id}")
    csrf_token = get_response.cookies["csrf_token"]

    response = client.post(
        f"/video-projects/{project.id}/pipeline/{action}",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    assert location.path == f"/video-projects/{project.id}"
    query = parse_qs(location.query)
    assert query["task_id"] == ["task-abc"]
    assert query["task_label"] == [label]


def test_pipeline_action_missing_project_returns_404(
    client: TestClient, db_session: Session
) -> None:
    topic = _make_topic(db_session)
    project = _make_video_project(db_session, topic)
    get_response = client.get(f"/video-projects/{project.id}")
    csrf_token = get_response.cookies["csrf_token"]

    response = client.post(
        "/video-projects/does-not-exist/pipeline/prepare-assets",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert response.status_code == 404
