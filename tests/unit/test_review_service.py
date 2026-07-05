from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.models.asset import Asset
from app.models.channel import Channel
from app.models.job_run import JobRun
from app.models.review import Review
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.providers.llm.fake import DeterministicFakeLLMProvider
from app.services.llm_gateway import BudgetExceededError
from app.services.reviews import machine, service


def _make_project(db_session: Session, tmp_path: Path) -> VideoProject:
    channel = Channel(name="ch")
    db_session.add(channel)
    db_session.flush()

    topic = Topic(channel_id=channel.id, title="topic", source_type="manual", source_ref="r-1")
    db_session.add(topic)
    db_session.flush()

    script = Script(
        topic_id=topic.id,
        version=1,
        title="良いタイトル",
        hook="hook",
        body={
            "title_candidates": ["良いタイトル"],
            "description": "説明文",
            "promised_outcome": "次の一歩が分かる",
            "sections": [{"heading": "本編", "narration": "解説します。", "evidence_ids": []}],
        },
        conclusion="まとめ",
        call_to_action="登録してください",
        source_manifest={"evidence_ids": []},
        status="reviewed",
    )
    db_session.add(script)
    db_session.flush()

    video_path = tmp_path / "out.mp4"
    video_path.write_bytes(b"fake mp4 bytes")

    srt_path = tmp_path / "sub.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:05,000\nhello\n", encoding="utf-8")

    project = VideoProject(
        topic_id=topic.id,
        script_id=script.id,
        status="VIDEO_RENDERED",
        generation=1,
        target_duration_seconds=10,
        output_path=str(video_path),
        checksum="c" * 64,
    )
    db_session.add(project)
    db_session.flush()

    db_session.add(
        Asset(
            video_project_id=project.id,
            asset_type="subtitle",
            role="subtitle:srt",
            file_path=str(srt_path),
            checksum="s" * 8,
            meta={"kind": "srt"},
        )
    )
    db_session.flush()
    return project


@pytest.fixture(autouse=True)
def _mock_machine_review_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """machine.inspect_machineが呼ぶffprobe/ffmpegを避け、常に合格させる。"""
    from app.services.media.probe import VideoProbeResult

    monkeypatch.setattr(
        machine,
        "probe_video",
        lambda path: VideoProbeResult(
            duration_seconds=10.0,
            width=1920,
            height=1080,
            fps=30.0,
            video_codec="h264",
            audio_codec="aac",
            has_audio=True,
            size_bytes=100,
        ),
    )
    monkeypatch.setattr(machine, "detect_silence_durations", lambda path: [])
    monkeypatch.setattr(machine, "detect_mean_volume_db", lambda path: -15.0)


def test_run_automated_review_passes_and_transitions_status(
    db_session: Session, tmp_path: Path
) -> None:
    project = _make_project(db_session, tmp_path)
    provider = DeterministicFakeLLMProvider()

    result = asyncio.run(
        service.run_automated_review(db_session, video_project_id=project.id, provider=provider)
    )
    db_session.commit()

    assert result.status == "AUTOMATED_REVIEW_PASSED"
    reviews = db_session.query(Review).filter(Review.video_project_id == project.id).all()
    assert {r.reviewer_type for r in reviews} == {"machine", "content"}
    assert all(r.passed for r in reviews)


def test_run_automated_review_is_idempotent_and_does_not_duplicate_reviews(
    db_session: Session, tmp_path: Path
) -> None:
    project = _make_project(db_session, tmp_path)
    provider = DeterministicFakeLLMProvider()

    asyncio.run(
        service.run_automated_review(db_session, video_project_id=project.id, provider=provider)
    )
    db_session.commit()
    first_count = db_session.query(Review).filter(Review.video_project_id == project.id).count()

    asyncio.run(
        service.run_automated_review(db_session, video_project_id=project.id, provider=provider)
    )
    db_session.commit()
    second_count = db_session.query(Review).filter(Review.video_project_id == project.id).count()

    assert first_count == second_count == 2
    assert db_session.query(JobRun).filter(JobRun.job_type == "automated_review").count() == 1


def test_run_automated_review_fails_to_review_failed_state_on_blocking_finding(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(db_session, tmp_path)
    provider = DeterministicFakeLLMProvider()

    monkeypatch.setattr(machine, "detect_silence_durations", lambda path: [12.0])

    result = asyncio.run(
        service.run_automated_review(db_session, video_project_id=project.id, provider=provider)
    )
    db_session.commit()

    assert result.status == "REVIEW_FAILED"


def test_budget_exceeded_propagates_and_leaves_status_unchanged(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(db_session, tmp_path)
    provider = DeterministicFakeLLMProvider()

    async def _raise_budget_exceeded(*args: object, **kwargs: object) -> None:
        raise BudgetExceededError("budget exceeded")

    monkeypatch.setattr(
        "app.services.reviews.content.call_llm",
        _raise_budget_exceeded,
    )

    with pytest.raises(BudgetExceededError):
        asyncio.run(
            service.run_automated_review(db_session, video_project_id=project.id, provider=provider)
        )
    db_session.commit()

    refreshed = db_session.get(VideoProject, project.id)
    assert refreshed is not None
    assert refreshed.status == "VIDEO_RENDERED"
    assert db_session.query(Review).filter(Review.video_project_id == project.id).count() == 0
    job_run = (
        db_session.query(JobRun)
        .filter(JobRun.job_type == "automated_review", JobRun.entity_id == project.id)
        .one()
    )
    assert job_run.status == "failed"


def test_restart_review_reverts_review_failed_to_video_rendered(
    db_session: Session, tmp_path: Path
) -> None:
    project = _make_project(db_session, tmp_path)
    from app.services.state_machine import transition

    transition(project, "REVIEW_FAILED")
    db_session.flush()

    result = service.restart_review(db_session, video_project_id=project.id)

    assert result.status == "VIDEO_RENDERED"
