"""投稿一覧ページの公開予約アクション(修正3)のテスト。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.approval import Approval
from app.models.channel import Channel
from app.models.review import Review
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.providers.youtube.fake import FakeYouTubeProvider
from app.services.media.renderer import compute_file_checksum
from app.services.publishing import uploader


def _make_uploaded_publication(db_session: Session, tmp_path: Path) -> str:
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
        body={"description": "説明文", "tags": ["tag1"]},
        source_manifest={},
        status="reviewed",
    )
    db_session.add(script)
    db_session.flush()

    video_path = tmp_path / "out.mp4"
    video_path.write_bytes(b"video-bytes")
    checksum = compute_file_checksum(video_path)

    project = VideoProject(
        topic_id=topic.id,
        script_id=script.id,
        status="UPLOAD_READY",
        generation=1,
        output_path=str(video_path),
        checksum=checksum,
    )
    db_session.add(project)
    db_session.flush()

    for reviewer_type in ("machine", "content"):
        db_session.add(
            Review(
                video_project_id=project.id,
                reviewer_type=reviewer_type,
                review_version=1,
                score=100.0,
                passed=True,
                findings=[],
                blocking_findings=[],
            )
        )
    db_session.add(Approval(video_project_id=project.id, decision="approved", decided_by="tester"))
    db_session.flush()

    provider = FakeYouTubeProvider()
    publication = asyncio.run(
        uploader.upload_video(db_session, video_project_id=project.id, provider=provider)
    )
    db_session.commit()
    return publication.id


def test_schedule_action_shows_rejection_reason_when_gate_disabled(
    client: TestClient, db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    publication_id = _make_uploaded_publication(db_session, tmp_path)

    get_response = client.get("/publications")
    csrf_token = get_response.cookies["csrf_token"]

    response = client.post(
        f"/publications/{publication_id}/schedule",
        data={"csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "auto publish disabled" in response.text


def test_schedule_action_without_csrf_token_is_forbidden(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    publication_id = _make_uploaded_publication(db_session, tmp_path)

    response = client.post(
        f"/publications/{publication_id}/schedule",
        data={"csrf_token": "invalid-token"},
    )

    assert response.status_code == 403


def test_schedule_action_missing_publication_returns_404(client: TestClient) -> None:
    get_response = client.get("/publications")
    csrf_token = get_response.cookies["csrf_token"]

    response = client.post(
        "/publications/does-not-exist/schedule",
        data={"csrf_token": csrf_token},
    )

    assert response.status_code == 404
