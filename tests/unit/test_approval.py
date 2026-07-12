from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.approval import Approval
from app.models.channel import Channel
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.services.reviews import approval
from app.services.state_machine import InvalidTransitionError


@dataclass
class _TaskResult:
    id: str = "rebuild-task"


def _make_project(db_session: Session, *, status: str = "AUTOMATED_REVIEW_PASSED") -> VideoProject:
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
        body={"title_candidates": ["タイトル"], "description": "説明"},
        source_manifest={},
        status="reviewed",
    )
    db_session.add(script)
    db_session.flush()

    project = VideoProject(topic_id=topic.id, script_id=script.id, status=status, generation=1)
    db_session.add(project)
    db_session.flush()
    return project


def test_approve_transitions_to_upload_ready_and_creates_approval(db_session: Session) -> None:
    project = _make_project(db_session)

    result = approval.approve(
        db_session, video_project_id=project.id, decided_by="reviewer@example.com", reason="OK"
    )
    db_session.commit()

    assert result.status == "UPLOAD_READY"
    approvals = db_session.query(Approval).filter(Approval.video_project_id == project.id).all()
    assert len(approvals) == 1
    assert approvals[0].decision == "approved"
    assert approvals[0].decided_by == "reviewer@example.com"


def test_reject_transitions_to_rejected(db_session: Session) -> None:
    project = _make_project(db_session)

    result = approval.reject(db_session, video_project_id=project.id, decided_by="reviewer")
    db_session.commit()

    assert result.status == "REJECTED"
    approvals = db_session.query(Approval).filter(Approval.video_project_id == project.id).all()
    assert len(approvals) == 1
    assert approvals[0].decision == "rejected"


def test_rebuild_from_script_creates_new_generation_and_preserves_rejected(
    db_session: Session,
) -> None:
    source = _make_project(db_session)
    source.production_settings = {"preset": "standard_5min"}
    approval.reject(db_session, video_project_id=source.id, decided_by="reviewer")

    replacement = approval.rebuild_from_script(db_session, video_project_id=source.id)
    db_session.commit()

    assert source.status == "REJECTED"
    assert replacement.id != source.id
    assert replacement.generation == 2
    assert replacement.status == "RESEARCH_READY"
    assert replacement.script_id is None
    assert replacement.production_settings == source.production_settings


def test_approve_from_invalid_state_raises_and_creates_no_approval(db_session: Session) -> None:
    project = _make_project(db_session, status="VIDEO_RENDERED")

    with pytest.raises(InvalidTransitionError):
        approval.approve(db_session, video_project_id=project.id, decided_by="reviewer")

    assert db_session.query(Approval).filter(Approval.video_project_id == project.id).count() == 0


def test_approve_missing_project_raises(db_session: Session) -> None:
    with pytest.raises(approval.VideoProjectNotFoundError):
        approval.approve(db_session, video_project_id="does-not-exist", decided_by="reviewer")


def test_web_get_review_sets_csrf_cookie_and_post_without_token_is_forbidden(
    client: TestClient, db_session: Session
) -> None:
    project = _make_project(db_session)
    db_session.commit()

    get_response = client.get(f"/video-projects/{project.id}/review")
    assert get_response.status_code == 200
    assert "csrf_token" in get_response.cookies

    post_response = client.post(
        f"/video-projects/{project.id}/approve",
        data={"csrf_token": "invalid-token"},
    )
    assert post_response.status_code == 403


def test_web_approve_with_valid_csrf_token_succeeds(
    client: TestClient, db_session: Session
) -> None:
    project = _make_project(db_session)
    db_session.commit()

    get_response = client.get(f"/video-projects/{project.id}/review")
    csrf_token = get_response.cookies["csrf_token"]

    post_response = client.post(
        f"/video-projects/{project.id}/approve",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert post_response.status_code == 303

    refreshed = db_session.get(VideoProject, project.id)
    assert refreshed is not None
    assert refreshed.status == "UPLOAD_READY"

    recorded = db_session.query(Approval).filter(Approval.video_project_id == project.id).one()
    assert recorded.decided_by == "dev-anonymous"


def test_web_approve_from_invalid_state_redirects_with_friendly_message_not_raw_json(
    client: TestClient, db_session: Session
) -> None:
    """二重送信等で既にUPLOAD_READY等まで進んだ後にもう一度承認POSTされた場合でも、
    生のJSON(HTTPException)を表示せず、レビュー画面へエラーメッセージ付きで戻す。
    """
    project = _make_project(db_session, status="VIDEO_RENDERED")
    db_session.commit()

    get_response = client.get(f"/video-projects/{project.id}/review")
    csrf_token = get_response.cookies["csrf_token"]

    post_response = client.post(
        f"/video-projects/{project.id}/approve",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert post_response.status_code == 303
    assert post_response.headers["location"].startswith(
        f"/video-projects/{project.id}/review?error="
    )
    assert db_session.query(Approval).filter(Approval.video_project_id == project.id).count() == 0


def test_web_reject_with_valid_csrf_token_succeeds(client: TestClient, db_session: Session) -> None:
    project = _make_project(db_session)
    db_session.commit()

    get_response = client.get(f"/video-projects/{project.id}/review")
    csrf_token = get_response.cookies["csrf_token"]

    post_response = client.post(
        f"/video-projects/{project.id}/reject",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert post_response.status_code == 303

    refreshed = db_session.get(VideoProject, project.id)
    assert refreshed is not None
    assert refreshed.status == "REJECTED"

    recorded = db_session.query(Approval).filter(Approval.video_project_id == project.id).one()
    assert recorded.decided_by == "dev-anonymous"


def test_web_rejected_project_can_rebuild_from_script(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _make_project(db_session)
    approval.reject(db_session, video_project_id=project.id, decided_by="reviewer")
    db_session.commit()
    captured: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "app.web.approvals.generate_script_task.delay",
        lambda topic_id, target_id, regeneration_key: (
            captured.append((topic_id, target_id, regeneration_key)) or _TaskResult()
        ),
    )

    page = client.get(f"/video-projects/{project.id}/review")
    assert "台本から作り直す" in page.text
    csrf_token = page.cookies["csrf_token"]
    response = client.post(
        f"/video-projects/{project.id}/rebuild-from-script",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    replacement = (
        db_session.query(VideoProject)
        .filter(VideoProject.topic_id == project.topic_id, VideoProject.generation == 2)
        .one()
    )
    assert captured == [(project.topic_id, replacement.id, replacement.id)]
    assert response.headers["location"].startswith(f"/video-projects/{replacement.id}?")
