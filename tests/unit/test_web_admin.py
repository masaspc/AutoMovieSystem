"""管理画面(Phase 7A)の全ページ・CSRF・パストラバーサル・ジョブ再実行・利用状況表示のテスト。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.comment import Comment
from app.models.insight import Insight
from app.models.job_run import JobRun
from app.models.publication import Publication
from app.models.review import Review
from app.models.script import Script
from app.models.topic import Topic
from app.models.usage_record import UsageRecord
from app.models.video_metric_daily import VideoMetricDaily
from app.models.video_project import VideoProject
from app.services.media.renderer import compute_file_checksum

pytestmark = pytest.mark.usefixtures("_clear_settings_cache")


def _make_channel(db_session: Session) -> Channel:
    channel = Channel(name="channel-1")
    db_session.add(channel)
    db_session.flush()
    return channel


def _make_topic(db_session: Session, channel: Channel, *, source_ref: str = "topic-1") -> Topic:
    topic = Topic(
        channel_id=channel.id,
        title="タイトル候補",
        description="概要",
        source_type="manual",
        source_ref=source_ref,
    )
    db_session.add(topic)
    db_session.flush()
    return topic


def _make_script(db_session: Session, topic: Topic) -> Script:
    script = Script(
        topic_id=topic.id,
        version=1,
        title="台本タイトル",
        body={
            "title_candidates": ["台本タイトル"],
            "description": "説明",
            "sections": [{"heading": "導入", "narration": "こんにちは。", "evidence_ids": []}],
        },
        source_manifest={},
        status="reviewed",
    )
    db_session.add(script)
    db_session.flush()
    return script


def _make_project(
    db_session: Session, topic: Topic, script: Script, *, status: str = "AUTOMATED_REVIEW_PASSED"
) -> VideoProject:
    project = VideoProject(topic_id=topic.id, script_id=script.id, status=status, generation=1)
    db_session.add(project)
    db_session.flush()
    return project


def _seed_full_fixture(db_session: Session) -> VideoProject:
    channel = _make_channel(db_session)
    topic = _make_topic(db_session, channel)
    script = _make_script(db_session, topic)
    project = _make_project(db_session, topic, script)

    db_session.add(
        Review(
            video_project_id=project.id,
            reviewer_type="content",
            review_version=1,
            score=70.0,
            passed=False,
            findings=[{"code": "x", "severity": "blocking", "message": "問題があります"}],
            blocking_findings=[{"code": "x", "severity": "blocking", "message": "問題があります"}],
        )
    )
    publication = Publication(
        video_project_id=project.id,
        title="動画タイトル",
        description="説明",
        tags=["tag"],
        privacy_status="private",
        idempotency_key=f"upload:{project.id}:checksum",
        upload_status="completed",
        youtube_video_id="fakevideo123",
    )
    db_session.add(publication)
    db_session.flush()

    db_session.add(
        VideoMetricDaily(
            publication_id=publication.id,
            metric_date=date.today(),
            views=100,
            ctr=0.05,
            average_view_percentage=0.4,
            comments_count=1,
        )
    )
    db_session.add(
        Comment(
            publication_id=publication.id,
            youtube_comment_id="c1",
            author_hash="hash1",
            text="次回はRustの解説をお願いします",
            published_at=datetime.now(UTC),
            category="NEXT_TOPIC_REQUEST",
        )
    )
    db_session.add(
        Insight(
            source_type="publication",
            source_id=publication.id,
            insight_type="next_topic_request",
            source_ref="ref-1",
            finding="次回企画候補があります",
            evidence={"count": 3},
            confidence=0.9,
            recommended_action="レビューする",
            human_review_reason="要確認",
        )
    )
    db_session.add(
        JobRun(
            job_type="render_video",
            entity_type="video_project",
            entity_id=project.id,
            idempotency_key=f"render_video:{project.id}:checksum",
            status="failed",
            last_error="simulated failure",
        )
    )
    db_session.add(
        UsageRecord(
            provider="fake",
            model="fake-model",
            operation="generate_script",
            input_tokens=100,
            output_tokens=50,
            estimated_cost_micro_usd=1_000,
            success=True,
        )
    )
    db_session.commit()
    return project


def test_all_twelve_admin_pages_return_200(client: TestClient, db_session: Session) -> None:
    project = _seed_full_fixture(db_session)
    topic = db_session.get(VideoProject, project.id).topic_id

    pages = [
        "/dashboard",
        "/topics",
        f"/topics/{topic}",
        "/video-projects",
        f"/video-projects/{project.id}",
        "/reviews",
        "/publications",
        "/comments",
        "/insights",
        "/jobs",
        "/usage",
        "/settings",
    ]
    for page in pages:
        response = client.get(page)
        detail = f"{page} -> {response.status_code}: {response.text[:300]}"
        assert response.status_code == 200, detail


def test_video_project_detail_shows_blocking_findings_and_approval_button(
    client: TestClient, db_session: Session
) -> None:
    project = _seed_full_fixture(db_session)

    response = client.get(f"/video-projects/{project.id}")
    assert response.status_code == 200
    assert 'data-testid="blocking-finding"' in response.text
    assert "承認" in response.text


def test_post_without_csrf_token_is_forbidden(client: TestClient, db_session: Session) -> None:
    channel = _make_channel(db_session)
    db_session.commit()

    response = client.post(
        "/topics",
        data={"channel_id": channel.id, "title": "新企画", "csrf_token": "invalid"},
    )
    assert response.status_code == 403


def test_post_topic_create_with_valid_csrf_redirects_and_persists(
    client: TestClient, db_session: Session
) -> None:
    channel = _make_channel(db_session)
    db_session.commit()

    get_response = client.get("/topics")
    csrf_token = get_response.cookies["csrf_token"]

    response = client.post(
        "/topics",
        data={"channel_id": channel.id, "title": "新企画", "csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert response.status_code == 303

    created = db_session.query(Topic).filter(Topic.title == "新企画").one_or_none()
    assert created is not None
    assert created.channel_id == channel.id


def test_media_serves_existing_file_within_generated_dir(
    client: TestClient, db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GENERATED_DIR", str(tmp_path))

    channel = _make_channel(db_session)
    topic = _make_topic(db_session, channel)
    script = _make_script(db_session, topic)
    project = _make_project(db_session, topic, script, status="UPLOAD_READY")

    video_dir = tmp_path / "videos" / project.id
    video_dir.mkdir(parents=True)
    video_path = video_dir / "video_abc123.mp4"
    video_path.write_bytes(b"fake-mp4-bytes")
    project.output_path = str(video_path)
    project.checksum = compute_file_checksum(video_path)
    db_session.commit()

    response = client.get(f"/media/{project.id}")
    assert response.status_code == 200
    assert response.content == b"fake-mp4-bytes"


def test_media_rejects_path_outside_generated_dir(
    client: TestClient, db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GENERATED_DIR", str(tmp_path / "generated"))
    (tmp_path / "generated").mkdir()

    channel = _make_channel(db_session)
    topic = _make_topic(db_session, channel)
    script = _make_script(db_session, topic)
    project = _make_project(db_session, topic, script, status="UPLOAD_READY")

    outside_path = tmp_path / "outside" / "secret.mp4"
    outside_path.parent.mkdir(parents=True)
    outside_path.write_bytes(b"top-secret")
    project.output_path = str(outside_path)
    project.checksum = "irrelevant"
    db_session.commit()

    response = client.get(f"/media/{project.id}")
    assert response.status_code == 400


def test_media_missing_project_returns_404(client: TestClient) -> None:
    response = client.get("/media/does-not-exist")
    assert response.status_code == 404


def test_jobs_retry_render_failure_invokes_recovery_and_rerun(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = _make_channel(db_session)
    topic = _make_topic(db_session, channel)
    script = _make_script(db_session, topic)
    project = _make_project(db_session, topic, script, status="RENDER_FAILED")

    job = JobRun(
        job_type="render_video",
        entity_type="video_project",
        entity_id=project.id,
        idempotency_key=f"render_video:{project.id}:checksum",
        status="failed",
        last_error="simulated ffmpeg failure",
    )
    db_session.add(job)
    db_session.commit()

    calls: dict[str, int] = {"restart": 0, "render": 0}

    def _fake_restart_render(session: Session, *, video_project_id: str) -> VideoProject:
        calls["restart"] += 1
        proj = session.get(VideoProject, video_project_id)
        proj.status = "ASSETS_READY"
        return proj

    def _fake_render_video(session: Session, *, video_project_id: str) -> VideoProject:
        calls["render"] += 1
        proj = session.get(VideoProject, video_project_id)
        proj.status = "VIDEO_RENDERED"
        return proj

    import app.web.jobs_page as jobs_page

    monkeypatch.setattr(jobs_page, "restart_render", _fake_restart_render)
    monkeypatch.setattr(jobs_page, "render_video", _fake_render_video)

    get_response = client.get("/jobs")
    csrf_token = get_response.cookies["csrf_token"]

    response = client.post(
        f"/jobs/{job.id}/retry",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert calls["restart"] == 1
    assert calls["render"] == 1

    refreshed = db_session.get(VideoProject, project.id)
    assert refreshed is not None
    assert refreshed.status == "VIDEO_RENDERED"


def test_jobs_retry_unknown_job_type_requires_manual_intervention(
    client: TestClient, db_session: Session
) -> None:
    job = JobRun(
        job_type="unknown_job_type",
        entity_type="video_project",
        entity_id="does-not-matter",
        idempotency_key="unknown:1",
        status="failed",
        last_error="unexpected",
    )
    db_session.add(job)
    db_session.commit()

    get_response = client.get("/jobs")
    csrf_token = get_response.cookies["csrf_token"]

    response = client.post(
        f"/jobs/{job.id}/retry",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "info=" in response.headers["location"]


def test_usage_page_reflects_usage_record(client: TestClient, db_session: Session) -> None:
    record = UsageRecord(
        provider="fake",
        model="fake-model",
        operation="generate_script",
        input_tokens=10,
        output_tokens=5,
        estimated_cost_micro_usd=12_345,
        success=True,
    )
    db_session.add(record)
    db_session.commit()

    response = client.get("/usage")
    assert response.status_code == 200
    assert "12345" in response.text
