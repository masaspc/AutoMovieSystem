"""成長画面の最小チェック承認キューと手動自動運転トリガー。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.approval import Approval
from app.models.asset import Asset
from app.models.channel import Channel
from app.models.evidence import Evidence
from app.models.review import Review
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject


@dataclass(frozen=True)
class _TaskResult:
    id: str


def _approval_candidate(db_session: Session) -> tuple[Channel, VideoProject]:
    channel = Channel(name="自動運転チャンネル", default_privacy_status="private")
    db_session.add(channel)
    db_session.flush()
    topic = Topic(
        channel_id=channel.id,
        title="企画タイトル",
        source_type="trend",
        source_ref="trend:web-approval",
        source_url="https://example.com/news",
    )
    db_session.add(topic)
    db_session.flush()
    script = Script(
        topic_id=topic.id,
        version=1,
        title="確認する動画タイトル",
        body={"description": "説明", "sections": []},
        source_manifest={
            "growth_quality_report": {
                "overall_score": 92,
                "issues": [],
                "human_check_reasons": ["固有名詞を最終確認"],
            },
            "growth_quick_approval_ready": True,
        },
        status="reviewed",
    )
    db_session.add(script)
    db_session.flush()
    project = VideoProject(
        topic_id=topic.id,
        script_id=script.id,
        status="AUTOMATED_REVIEW_PASSED",
        generation=1,
        output_path="generated/videos/example/output.mp4",
        checksum="a" * 64,
    )
    db_session.add(project)
    db_session.flush()
    db_session.add_all(
        [
            Review(
                video_project_id=project.id,
                reviewer_type="machine",
                review_version=1,
                score=0.98,
                passed=True,
            ),
            Review(
                video_project_id=project.id,
                reviewer_type="content",
                review_version=1,
                score=0.93,
                passed=True,
            ),
            Evidence(
                topic_id=topic.id,
                source_url="https://example.com/source",
                source_title="一次情報",
                publisher="Example",
                claim="確認済みの要点",
                excerpt_hash="b" * 64,
                verification_status="verified",
            ),
            Asset(
                video_project_id=project.id,
                asset_type="image",
                role="thumbnail",
                file_path="generated/videos/example/thumbnails/selected.png",
                source="generated",
                checksum="c" * 64,
                meta={"candidate_index": 0},
            ),
        ]
    )
    db_session.commit()
    return channel, project


def test_growth_page_shows_minimal_approval_context(
    client: TestClient, db_session: Session
) -> None:
    _approval_candidate(db_session)

    response = client.get("/growth")

    assert response.status_code == 200
    assert "最小チェック承認キュー (1件)" in response.text
    assert "自動運転チャンネル" in response.text
    assert "企画タイトル" in response.text
    assert "確認する動画タイトル" in response.text
    assert "selected.png" in response.text
    assert "machine 0.98" in response.text
    assert "content 0.93" in response.text
    assert "クイック承認可" in response.text
    assert "固有名詞を最終確認" in response.text
    assert "AI開示: true" in response.text
    assert "公開範囲: private" in response.text
    assert "一次情報" in response.text
    assert "動画をプレビュー" in response.text
    assert "承認してYouTubeへ非公開アップロード" in response.text


def test_growth_page_defensively_falls_back_without_quality_manifest(
    client: TestClient, db_session: Session
) -> None:
    _, project = _approval_candidate(db_session)
    script = db_session.get(Script, project.script_id)
    assert script is not None
    script.source_manifest = {}
    db_session.commit()

    response = client.get("/growth")

    assert "品質プリフライト未実施" in response.text
    assert "詳細確認が必要です" in response.text


def test_growth_private_upload_approval_is_csrf_protected(
    client: TestClient, db_session: Session
) -> None:
    _, project = _approval_candidate(db_session)

    response = client.post(
        f"/growth/approval-queue/{project.id}/approve-private-upload",
        data={"csrf_token": "invalid"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert db_session.query(Approval).count() == 0


def test_growth_private_upload_records_operator_and_is_retry_safe(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, project = _approval_candidate(db_session)
    dispatched: list[str] = []
    monkeypatch.setattr(
        "app.web.growth_page.upload_video_task.delay",
        lambda project_id: (
            dispatched.append(project_id) or _TaskResult(id=f"upload-{len(dispatched)}")
        ),
    )
    csrf_token = client.get("/growth").cookies["csrf_token"]

    first = client.post(
        f"/growth/approval-queue/{project.id}/approve-private-upload",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    second = client.post(
        f"/growth/approval-queue/{project.id}/approve-private-upload",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert first.status_code == 303
    assert "task_id=upload-1" in first.headers["location"]
    assert second.status_code == 303
    assert "task_id=upload-2" in second.headers["location"]
    db_session.expire_all()
    refreshed = db_session.get(VideoProject, project.id)
    assert refreshed is not None and refreshed.status == "UPLOAD_READY"
    approvals = db_session.query(Approval).filter_by(video_project_id=project.id).all()
    assert len(approvals) == 1
    assert approvals[0].decided_by == "dev-anonymous"
    assert dispatched == [project.id, project.id]


def test_run_now_only_dispatches_when_autopilot_is_fully_configured(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = Channel(name="configured")
    db_session.add(channel)
    db_session.commit()
    monkeypatch.setenv("GROWTH_AUTOPILOT_ENABLED", "true")
    monkeypatch.setenv("GROWTH_AUTOPILOT_CHANNEL_ID", channel.id)
    get_settings.cache_clear()
    dispatched = 0

    def _delay() -> _TaskResult:
        nonlocal dispatched
        dispatched += 1
        return _TaskResult(id="autopilot-task")

    monkeypatch.setattr("app.web.growth_page.run_daily_autopilot.delay", _delay)
    page = client.get("/growth")
    assert "今すぐ候補を収集" in page.text
    response = client.post(
        "/growth/autopilot/run-now",
        data={"csrf_token": page.cookies["csrf_token"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "task_id=autopilot-task" in response.headers["location"]
    assert dispatched == 1


def test_run_now_rejects_disabled_autopilot(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROWTH_AUTOPILOT_ENABLED", "false")
    get_settings.cache_clear()
    page = client.get("/growth")

    response = client.post(
        "/growth/autopilot/run-now",
        data={"csrf_token": page.cookies["csrf_token"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/growth?error=")
