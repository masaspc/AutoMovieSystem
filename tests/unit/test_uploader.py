"""アップロードサービス(ADR-0005 2段階記録+reconcile)の単体テスト。実APIは呼ばない(Fake使用)。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.models.approval import Approval
from app.models.channel import Channel
from app.models.job_run import JobRun
from app.models.publication import Publication
from app.models.review import Review
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.providers.youtube.base import QuotaExceededError, UploadRequest, UploadResult
from app.providers.youtube.fake import FakeYouTubeProvider
from app.services.media.renderer import compute_file_checksum
from app.services.publishing import uploader


def _make_project(
    db_session: Session, tmp_path: Path, *, status: str = "UPLOAD_READY"
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
        title="タイトル",
        body={"description": "説明文", "tags": ["tag1", "tag2"]},
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
        status=status,
        generation=1,
        output_path=str(video_path),
        checksum=checksum,
    )
    db_session.add(project)
    db_session.flush()
    return project


def _add_passing_reviews(db_session: Session, project: VideoProject) -> None:
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
    db_session.flush()


def _approve(db_session: Session, project: VideoProject) -> None:
    db_session.add(Approval(video_project_id=project.id, decision="approved", decided_by="tester"))
    db_session.flush()


def _make_ready_project(db_session: Session, tmp_path: Path) -> VideoProject:
    project = _make_project(db_session, tmp_path)
    _add_passing_reviews(db_session, project)
    _approve(db_session, project)
    return project


def test_upload_missing_approval_is_rejected(db_session: Session, tmp_path: Path) -> None:
    project = _make_project(db_session, tmp_path)
    _add_passing_reviews(db_session, project)
    provider = FakeYouTubeProvider()

    with pytest.raises(uploader.UploadPreconditionError, match="approval"):
        asyncio.run(
            uploader.upload_video(db_session, video_project_id=project.id, provider=provider)
        )

    assert db_session.query(Publication).count() == 0
    assert db_session.get(VideoProject, project.id).status == project.status


def test_upload_checksum_mismatch_is_rejected(db_session: Session, tmp_path: Path) -> None:
    project = _make_ready_project(db_session, tmp_path)
    project.checksum = "0" * 64
    db_session.flush()
    provider = FakeYouTubeProvider()

    with pytest.raises(uploader.UploadPreconditionError, match="checksum"):
        asyncio.run(
            uploader.upload_video(db_session, video_project_id=project.id, provider=provider)
        )

    assert db_session.query(Publication).count() == 0


def test_upload_wrong_status_is_rejected(db_session: Session, tmp_path: Path) -> None:
    project = _make_project(db_session, tmp_path, status="VIDEO_RENDERED")
    _add_passing_reviews(db_session, project)
    _approve(db_session, project)
    provider = FakeYouTubeProvider()

    with pytest.raises(uploader.UploadPreconditionError, match="UPLOAD_READY"):
        asyncio.run(
            uploader.upload_video(db_session, video_project_id=project.id, provider=provider)
        )


def test_successful_upload_transitions_state_and_records_publication(
    db_session: Session, tmp_path: Path
) -> None:
    project = _make_ready_project(db_session, tmp_path)
    provider = FakeYouTubeProvider()

    publication = asyncio.run(
        uploader.upload_video(db_session, video_project_id=project.id, provider=provider)
    )
    db_session.commit()

    assert publication.upload_status == "completed"
    assert publication.youtube_video_id
    refreshed = db_session.get(VideoProject, project.id)
    assert refreshed.status == "UPLOADED_PRIVATE"

    # description にidempotencyマーカーが埋め込まれていること。
    stored = provider.store.videos[publication.youtube_video_id]
    assert uploader.build_idempotency_marker(publication.idempotency_key) in stored.description


def test_upload_twice_creates_only_one_publication_no_duplicate_call(
    db_session: Session, tmp_path: Path
) -> None:
    """2回実行してもPublicationは1件のみ、実アップロードは1回のみ(冪等性)。"""
    project = _make_ready_project(db_session, tmp_path)
    provider = FakeYouTubeProvider()

    upload_calls = {"count": 0}
    original_upload = provider.upload_video

    async def _counting_upload(*, request: UploadRequest) -> UploadResult:
        upload_calls["count"] += 1
        return await original_upload(request=request)

    provider.upload_video = _counting_upload  # type: ignore[method-assign]

    publication1 = asyncio.run(
        uploader.upload_video(db_session, video_project_id=project.id, provider=provider)
    )
    db_session.commit()
    publication2 = asyncio.run(
        uploader.upload_video(db_session, video_project_id=project.id, provider=provider)
    )
    db_session.commit()

    assert publication1.id == publication2.id
    assert publication1.youtube_video_id == publication2.youtube_video_id
    assert db_session.query(Publication).count() == 1
    assert upload_calls["count"] == 1


def test_reconcile_after_crash_finds_existing_video_without_reupload(
    db_session: Session, tmp_path: Path
) -> None:
    """started+youtube_video_id空(クラッシュ疑い)から、reconcileで既存動画を検出する。"""
    project = _make_ready_project(db_session, tmp_path)
    idempotency_key = uploader.build_upload_idempotency_key(project.id, project.checksum)
    marker = uploader.build_idempotency_marker(idempotency_key)

    provider = FakeYouTubeProvider()
    # クラッシュ前にYouTube側にはアップロード済みだったと仮定して直接ストアへ投入する。
    orphan_result = asyncio.run(
        provider.upload_video(
            request=UploadRequest(
                file_path=str(project.output_path),
                title="タイトル",
                description="説明文",
                tags=["tag1", "tag2"],
                privacy_status="private",
                idempotency_marker=marker,
            )
        )
    )

    # JobRun/Publicationがstarted状態のまま残っている状況を再現する(lease超過=stale)。
    db_session.add(
        JobRun(
            job_type="upload_video",
            entity_type="video_project",
            entity_id=project.id,
            idempotency_key=idempotency_key,
            status="started",
            started_at=datetime.utcnow() - timedelta(hours=2),
        )
    )
    db_session.add(
        Publication(
            video_project_id=project.id,
            title="タイトル",
            description="説明文",
            tags=["tag1", "tag2"],
            privacy_status="private",
            idempotency_key=idempotency_key,
            upload_status="started",
        )
    )
    db_session.flush()

    upload_calls = {"count": 0}
    original_upload = provider.upload_video

    async def _counting_upload(*, request: UploadRequest) -> UploadResult:
        upload_calls["count"] += 1
        return await original_upload(request=request)

    provider.upload_video = _counting_upload  # type: ignore[method-assign]

    publication = asyncio.run(
        uploader.upload_video(db_session, video_project_id=project.id, provider=provider)
    )
    db_session.commit()

    assert publication.upload_status == "completed"
    assert publication.youtube_video_id == orphan_result.youtube_video_id
    assert upload_calls["count"] == 0  # reconcileで見つかったため再アップロードしない
    refreshed = db_session.get(VideoProject, project.id)
    assert refreshed.status == "UPLOADED_PRIVATE"


def test_reconcile_not_found_falls_back_to_real_upload(db_session: Session, tmp_path: Path) -> None:
    """reconcileで見つからない場合はアップロードを実行する。"""
    project = _make_ready_project(db_session, tmp_path)
    idempotency_key = uploader.build_upload_idempotency_key(project.id, project.checksum)

    provider = FakeYouTubeProvider()  # ストアは空(マーカー未登録)

    db_session.add(
        JobRun(
            job_type="upload_video",
            entity_type="video_project",
            entity_id=project.id,
            idempotency_key=idempotency_key,
            status="started",
            started_at=datetime.utcnow() - timedelta(hours=2),
        )
    )
    db_session.add(
        Publication(
            video_project_id=project.id,
            title="タイトル",
            description="説明文",
            tags=["tag1", "tag2"],
            privacy_status="private",
            idempotency_key=idempotency_key,
            upload_status="started",
        )
    )
    db_session.flush()

    publication = asyncio.run(
        uploader.upload_video(db_session, video_project_id=project.id, provider=provider)
    )
    db_session.commit()

    assert publication.upload_status == "completed"
    assert publication.youtube_video_id is not None


def test_quota_exceeded_stops_without_retry_and_marks_failed(
    db_session: Session, tmp_path: Path
) -> None:
    project = _make_ready_project(db_session, tmp_path)
    provider = FakeYouTubeProvider()

    call_count = {"n": 0}

    async def _raise_quota(*, request: UploadRequest) -> UploadResult:
        call_count["n"] += 1
        raise QuotaExceededError("quota exceeded (test)")

    provider.upload_video = _raise_quota  # type: ignore[method-assign]

    with pytest.raises(QuotaExceededError):
        asyncio.run(
            uploader.upload_video(db_session, video_project_id=project.id, provider=provider)
        )
    db_session.commit()

    assert call_count["n"] == 1  # リトライしない
    publication = (
        db_session.query(Publication).filter(Publication.video_project_id == project.id).one()
    )
    assert publication.upload_status == "failed"
    assert publication.last_error is not None
    refreshed = db_session.get(VideoProject, project.id)
    assert refreshed.status == "UPLOAD_FAILED"


def test_restart_upload_recovers_from_failed_state(db_session: Session, tmp_path: Path) -> None:
    project = _make_ready_project(db_session, tmp_path)
    provider = FakeYouTubeProvider()

    async def _raise_quota(*, request: UploadRequest) -> UploadResult:
        raise QuotaExceededError("quota exceeded (test)")

    provider.upload_video = _raise_quota  # type: ignore[method-assign]
    with pytest.raises(QuotaExceededError):
        asyncio.run(
            uploader.upload_video(db_session, video_project_id=project.id, provider=provider)
        )
    db_session.commit()

    restored = uploader.restart_upload(db_session, video_project_id=project.id)
    db_session.commit()
    assert restored.status == "UPLOAD_READY"
