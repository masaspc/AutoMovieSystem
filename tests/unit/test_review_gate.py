from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.approval import Approval
from app.models.channel import Channel
from app.models.review import Review
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.services.reviews.gate import can_auto_publish


def _make_project(
    db_session: Session, tmp_path: Path, *, title: str = "普通のタイトル"
) -> VideoProject:
    channel = Channel(name="ch")
    db_session.add(channel)
    db_session.flush()

    topic = Topic(channel_id=channel.id, title="topic", source_type="manual", source_ref="r-1")
    db_session.add(topic)
    db_session.flush()

    script = Script(
        topic_id=topic.id,
        version=1,
        title=title,
        body={"title_candidates": [title], "description": "説明"},
        source_manifest={},
        status="reviewed",
    )
    db_session.add(script)
    db_session.flush()

    video_path = tmp_path / "out.mp4"
    video_path.write_bytes(b"content")
    from app.services.media.renderer import compute_file_checksum

    checksum = compute_file_checksum(video_path)

    project = VideoProject(
        topic_id=topic.id,
        script_id=script.id,
        status="AUTOMATED_REVIEW_PASSED",
        generation=1,
        target_duration_seconds=10,
        output_path=str(video_path),
        checksum=checksum,
    )
    db_session.add(project)
    db_session.flush()
    return project


def _add_passing_reviews(db_session: Session, project: VideoProject) -> None:
    for reviewer_type in ("machine", "content"):
        review = Review(
            video_project_id=project.id,
            reviewer_type=reviewer_type,
            review_version=1,
            score=100.0,
            passed=True,
            findings=[],
            blocking_findings=[],
        )
        db_session.add(review)
    db_session.flush()


def _approve(db_session: Session, project: VideoProject) -> None:
    db_session.add(Approval(video_project_id=project.id, decision="approved", decided_by="tester"))
    db_session.flush()


@pytest.fixture(autouse=True)
def _enable_auto_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_auto_publish_disabled_always_false(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    get_settings.cache_clear()
    project = _make_project(db_session, tmp_path)
    _add_passing_reviews(db_session, project)
    _approve(db_session, project)

    ok, reasons = can_auto_publish(db_session, project.id)

    assert ok is False
    assert reasons == ["auto publish disabled"]


def test_missing_reviews_blocks_publish(db_session: Session, tmp_path: Path) -> None:
    project = _make_project(db_session, tmp_path)

    ok, reasons = can_auto_publish(db_session, project.id)

    assert ok is False
    assert any("machine review" in r for r in reasons)
    assert any("content review" in r for r in reasons)


def test_missing_approval_blocks_publish_when_required(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REQUIRE_HUMAN_APPROVAL", "true")
    get_settings.cache_clear()
    project = _make_project(db_session, tmp_path)
    _add_passing_reviews(db_session, project)

    ok, reasons = can_auto_publish(db_session, project.id)

    assert ok is False
    assert any("approval" in r for r in reasons)


def test_blocking_findings_block_publish(db_session: Session, tmp_path: Path) -> None:
    project = _make_project(db_session, tmp_path)
    db_session.add(
        Review(
            video_project_id=project.id,
            reviewer_type="machine",
            review_version=1,
            score=0.0,
            passed=False,
            findings=[{"code": "x", "severity": "blocking", "message": "m", "detail": None}],
            blocking_findings=[
                {"code": "x", "severity": "blocking", "message": "m", "detail": None}
            ],
        )
    )
    db_session.add(
        Review(
            video_project_id=project.id,
            reviewer_type="content",
            review_version=1,
            score=100.0,
            passed=True,
            findings=[],
            blocking_findings=[],
        )
    )
    db_session.flush()
    _approve(db_session, project)

    ok, reasons = can_auto_publish(db_session, project.id)

    assert ok is False
    assert any("blocking findings" in r for r in reasons)


def test_checksum_mismatch_blocks_publish(db_session: Session, tmp_path: Path) -> None:
    project = _make_project(db_session, tmp_path)
    _add_passing_reviews(db_session, project)
    _approve(db_session, project)
    project.checksum = "wrong" * 12 + "0000"
    db_session.flush()

    ok, reasons = can_auto_publish(db_session, project.id)

    assert ok is False
    assert any("checksum" in r for r in reasons)


def test_high_risk_keyword_blocks_publish(db_session: Session, tmp_path: Path) -> None:
    project = _make_project(db_session, tmp_path, title="投資で儲ける方法")
    _add_passing_reviews(db_session, project)
    _approve(db_session, project)

    ok, reasons = can_auto_publish(db_session, project.id)

    assert ok is False
    assert any("high risk" in r for r in reasons)


def test_all_conditions_satisfied_allows_publish(db_session: Session, tmp_path: Path) -> None:
    project = _make_project(db_session, tmp_path)
    _add_passing_reviews(db_session, project)
    _approve(db_session, project)

    ok, reasons = can_auto_publish(db_session, project.id)

    assert ok is True
    assert reasons == []
