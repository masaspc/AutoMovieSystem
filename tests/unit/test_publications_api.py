from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.approval import Approval
from app.models.channel import Channel
from app.models.review import Review
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.services.media.renderer import compute_file_checksum


def _make_ready_project(db_session: Session, tmp_path: Path) -> VideoProject:
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
    db_session.commit()
    return project


def test_upload_video_endpoint_success(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    project = _make_ready_project(db_session, tmp_path)

    response = client.post(f"/api/video-projects/{project.id}/upload")

    assert response.status_code == 201
    body = response.json()
    assert body["video_project_id"] == project.id
    assert body["upload_status"] == "completed"
    assert body["youtube_video_id"]
    assert body["privacy_status"] == "private"


def test_upload_video_endpoint_missing_project_returns_404(client: TestClient) -> None:
    response = client.post("/api/video-projects/does-not-exist/upload")
    assert response.status_code == 404


def test_upload_video_endpoint_precondition_failure_returns_400(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
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
        body={},
        source_manifest={},
        status="reviewed",
    )
    db_session.add(script)
    db_session.flush()
    project = VideoProject(
        topic_id=topic.id, script_id=script.id, status="UPLOAD_READY", generation=1
    )
    db_session.add(project)
    db_session.commit()

    response = client.post(f"/api/video-projects/{project.id}/upload")

    assert response.status_code == 400


def test_schedule_publication_endpoint_rejected_when_auto_publish_disabled(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    project = _make_ready_project(db_session, tmp_path)
    upload_response = client.post(f"/api/video-projects/{project.id}/upload")
    publication_id = upload_response.json()["id"]

    response = client.post(
        f"/api/publications/{publication_id}/schedule",
        json={"publish_at": "2026-08-01T00:00:00Z"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["scheduled"] is False
    assert any("auto publish disabled" in r for r in body["reasons"])


def test_schedule_publication_endpoint_missing_publication_returns_404(client: TestClient) -> None:
    response = client.post(
        "/api/publications/does-not-exist/schedule",
        json={"publish_at": "2026-08-01T00:00:00Z"},
    )
    assert response.status_code == 404
