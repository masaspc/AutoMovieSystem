"""/video-projects 一覧・詳細のCSRF Cookie発行方針の統一を検証する(Phase 1)。

一覧ページから直接フォーム操作(詳細ページのPOST)へ進んでもCookieが未発行に
ならないよう、両ページとも共通ヘルパー(set_csrf_cookie)で同一属性のCookieを
発行することを確認する。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject


def _make_project(db_session: Session) -> VideoProject:
    channel = Channel(name="ch")
    db_session.add(channel)
    db_session.flush()
    topic = Topic(channel_id=channel.id, title="topic", source_type="manual", source_ref="r-1")
    db_session.add(topic)
    db_session.flush()
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


def test_video_projects_list_issues_csrf_cookie(client: TestClient, db_session: Session) -> None:
    response = client.get("/video-projects")
    assert response.status_code == 200
    assert "csrf_token" in response.cookies


def test_video_projects_detail_issues_csrf_cookie(client: TestClient, db_session: Session) -> None:
    project = _make_project(db_session)
    response = client.get(f"/video-projects/{project.id}")
    assert response.status_code == 200
    assert "csrf_token" in response.cookies


def test_list_and_detail_reuse_same_csrf_token_within_session(
    client: TestClient, db_session: Session
) -> None:
    """一覧→詳細と遷移してもトークンが張り替えられない(セッション内で安定)。"""
    project = _make_project(db_session)

    list_response = client.get("/video-projects")
    token_from_list = list_response.cookies["csrf_token"]

    detail_response = client.get(f"/video-projects/{project.id}")
    token_from_detail = detail_response.cookies["csrf_token"]

    assert token_from_list == token_from_detail
