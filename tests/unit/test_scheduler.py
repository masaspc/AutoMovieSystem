"""公開予約サービス(スケジューリングゲート)の単体テスト。実APIは呼ばない(Fake使用)。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.approval import Approval
from app.models.channel import Channel
from app.models.publication import Publication
from app.models.review import Review
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.providers.youtube.fake import FakeYouTubeProvider
from app.services.media.renderer import compute_file_checksum
from app.services.publishing import scheduler, uploader


@pytest.fixture(autouse=True)
def _enable_auto_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_uploaded_publication(
    db_session: Session, tmp_path: Path
) -> tuple[VideoProject, Publication, FakeYouTubeProvider]:
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
    return project, publication, provider


def test_schedule_rejected_when_auto_publish_disabled(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    get_settings.cache_clear()
    project, publication, provider = _make_uploaded_publication(db_session, tmp_path)

    result = asyncio.run(
        scheduler.schedule_publication(
            db_session,
            publication_id=publication.id,
            publish_at=datetime(2026, 8, 1, tzinfo=UTC),
            provider=provider,
        )
    )

    assert result.scheduled is False
    assert any("auto publish disabled" in r for r in result.reasons)
    refreshed = db_session.get(VideoProject, project.id)
    assert refreshed.status == "UPLOADED_PRIVATE"


def test_schedule_rejected_when_auth_invalid(db_session: Session, tmp_path: Path) -> None:
    project, publication, provider = _make_uploaded_publication(db_session, tmp_path)
    provider.store.auth_ok = False

    result = asyncio.run(
        scheduler.schedule_publication(
            db_session,
            publication_id=publication.id,
            publish_at=datetime(2026, 8, 1, tzinfo=UTC),
            provider=provider,
        )
    )

    assert result.scheduled is False
    assert any("auth" in r for r in result.reasons)


def test_schedule_rejected_when_duplicate_completed_publication_exists(
    db_session: Session, tmp_path: Path
) -> None:
    project, publication, provider = _make_uploaded_publication(db_session, tmp_path)
    # 別の完了済みPublicationが同一VideoProjectに存在する異常系を再現する。
    duplicate = Publication(
        video_project_id=project.id,
        youtube_video_id="other-video-id",
        title="別動画",
        description="",
        tags=[],
        privacy_status="private",
        idempotency_key="upload:duplicate:other",
        upload_status="completed",
    )
    db_session.add(duplicate)
    db_session.flush()

    result = asyncio.run(
        scheduler.schedule_publication(
            db_session,
            publication_id=publication.id,
            publish_at=datetime(2026, 8, 1, tzinfo=UTC),
            provider=provider,
        )
    )

    assert result.scheduled is False
    assert any("duplicate" in r for r in result.reasons)


def test_schedule_succeeds_when_all_gate_conditions_met(
    db_session: Session, tmp_path: Path
) -> None:
    project, publication, provider = _make_uploaded_publication(db_session, tmp_path)
    publish_at = datetime(2026, 8, 1, tzinfo=UTC)

    result = asyncio.run(
        scheduler.schedule_publication(
            db_session,
            publication_id=publication.id,
            publish_at=publish_at,
            provider=provider,
        )
    )
    db_session.commit()

    assert result.scheduled is True
    assert result.reasons == []
    refreshed = db_session.get(VideoProject, project.id)
    assert refreshed.status == "SCHEDULED"
    # SQLiteのDateTime列はtzinfoを保持しないため、naive化して比較する。
    assert publication.scheduled_at == publish_at.replace(tzinfo=None)
    stored_video = provider.store.videos[publication.youtube_video_id]
    assert stored_video.scheduled_at == publish_at


def test_schedule_missing_publication_raises(db_session: Session) -> None:
    provider = FakeYouTubeProvider()
    with pytest.raises(scheduler.PublicationNotFoundError):
        asyncio.run(
            scheduler.schedule_publication(
                db_session,
                publication_id="does-not-exist",
                publish_at=datetime(2026, 8, 1, tzinfo=UTC),
                provider=provider,
            )
        )


def test_finalize_due_publications_returns_zero_before_due_date(
    db_session: Session, tmp_path: Path
) -> None:
    project, publication, provider = _make_uploaded_publication(db_session, tmp_path)
    result = asyncio.run(
        scheduler.schedule_publication(
            db_session,
            publication_id=publication.id,
            publish_at=datetime(2099, 1, 1, tzinfo=UTC),
            provider=provider,
        )
    )
    db_session.commit()
    assert result.scheduled is True

    finalized_count = scheduler.finalize_due_publications(
        db_session, now=datetime(2026, 8, 1, tzinfo=UTC)
    )
    db_session.commit()

    assert finalized_count == 0
    refreshed_publication = db_session.get(Publication, publication.id)
    assert refreshed_publication.published_at is None
    refreshed_project = db_session.get(VideoProject, project.id)
    assert refreshed_project.status == "SCHEDULED"


def test_finalize_due_publications_sets_published_at_and_transitions_state(
    db_session: Session, tmp_path: Path
) -> None:
    project, publication, provider = _make_uploaded_publication(db_session, tmp_path)
    publish_at = datetime(2026, 1, 1, tzinfo=UTC)
    result = asyncio.run(
        scheduler.schedule_publication(
            db_session,
            publication_id=publication.id,
            publish_at=publish_at,
            provider=provider,
        )
    )
    db_session.commit()
    assert result.scheduled is True

    now = datetime(2026, 8, 1, tzinfo=UTC)
    finalized_count = scheduler.finalize_due_publications(db_session, now=now)
    db_session.commit()

    assert finalized_count == 1
    refreshed_publication = db_session.get(Publication, publication.id)
    assert refreshed_publication.published_at == now.replace(tzinfo=None)
    refreshed_project = db_session.get(VideoProject, project.id)
    assert refreshed_project.status == "METRICS_COLLECTING"

    # 2回目実行では対象外(published_at IS NULL条件を満たさない)。二重更新なし。
    second_run_count = scheduler.finalize_due_publications(db_session, now=now)
    db_session.commit()
    assert second_run_count == 0
    refreshed_publication_again = db_session.get(Publication, publication.id)
    assert refreshed_publication_again.published_at == now.replace(tzinfo=None)
