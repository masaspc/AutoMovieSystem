"""Celeryタスクラッパーの失敗永続化(D-017)の検証。

タスクラッパーは失敗時に自セッションを `rollback()` するため、サービス層内で
flushだけされたJobRun失敗記録・失敗状態遷移(RENDER_FAILED等)は本来消えてしまう。
本テストは、新規セッション経由の再永続化(`record_failure_in_new_session` /
`apply_failure_transition_in_new_session`)により、rollback後も別セッションから
読めることを確認する。

SQLite in-memory はセッション(接続)ごとに別データベースになり共有できないため、
`tmp_path` 配下のファイルDBを使う。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db.session as db_session_module
import app.workers.tasks.media as media_tasks
import app.workers.tasks.publishing as publishing_tasks
from app.db.base import Base
from app.db.session import enable_sqlite_foreign_keys
from app.models.approval import Approval
from app.models.asset import Asset
from app.models.channel import Channel
from app.models.job_run import JobRun
from app.models.publication import Publication
from app.models.review import Review
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.providers.youtube.base import UploadRequest, UploadResult
from app.services.media import renderer
from app.services.media.pipeline import PipelineRenderError
from app.services.media.renderer import compute_file_checksum


@pytest.fixture
def file_db_session_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sessionmaker:
    """tmp_path配下のファイルDBに、タスクラッパー本体+失敗記録ヘルパーの両方を向ける。"""
    db_path = tmp_path / "task_failure.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}, future=True
    )
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    # タスクラッパー自身が使う SessionLocal(モジュールに束縛済みの名前)を差し替える。
    monkeypatch.setattr(media_tasks, "SessionLocal", factory)
    monkeypatch.setattr(publishing_tasks, "SessionLocal", factory)
    # record_failure_in_new_session / apply_failure_transition_in_new_session は
    # `from app.db.session import SessionLocal` を呼び出し時に遅延実行するため、
    # 参照元である app.db.session.SessionLocal を差し替えれば反映される。
    monkeypatch.setattr(db_session_module, "SessionLocal", factory)

    return factory


def _make_video_project_ready_for_render(session, tmp_path: Path) -> VideoProject:
    channel = Channel(name="ch")
    session.add(channel)
    session.flush()

    topic = Topic(channel_id=channel.id, title="topic", source_type="manual", source_ref="r-1")
    session.add(topic)
    session.flush()

    script = Script(
        topic_id=topic.id,
        version=1,
        title="タイトル",
        hook="hook",
        body={
            "sections": [
                {"heading": "導入", "narration": "こんにちは。", "evidence_ids": []},
            ]
        },
        source_manifest={},
        status="reviewed",
    )
    session.add(script)
    session.flush()

    project = VideoProject(
        topic_id=topic.id,
        script_id=script.id,
        status="ASSETS_READY",
        generation=1,
        aspect_ratio="16:9",
    )
    session.add(project)
    session.flush()

    bg_path = tmp_path / "bg.png"
    bg_path.write_bytes(b"bg")
    audio_path = tmp_path / "a0.wav"
    audio_path.write_bytes(b"audio")

    session.add(
        Asset(
            video_project_id=project.id,
            asset_type="image",
            role="background",
            file_path=str(bg_path),
            checksum="bgsum",
            meta={"role": "background"},
        )
    )
    session.add(
        Asset(
            video_project_id=project.id,
            asset_type="audio",
            role="audio:0",
            file_path=str(audio_path),
            checksum="audiosum",
            meta={"section_index": 0, "duration_seconds": 1.0, "sample_rate": 16000},
        )
    )
    session.flush()
    return project


def test_render_video_task_persists_job_failure_and_render_failed_after_rollback(
    file_db_session_factory: sessionmaker,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERATED_DIR", str(tmp_path / "generated"))

    setup_session = file_db_session_factory()
    project = _make_video_project_ready_for_render(setup_session, tmp_path)
    project_id = project.id
    setup_session.commit()
    setup_session.close()

    def _raise_render_error(*args: object, **kwargs: object) -> None:
        raise renderer.RenderError("simulated ffmpeg failure")

    monkeypatch.setattr(renderer, "render_video", _raise_render_error)

    with pytest.raises(PipelineRenderError):
        media_tasks.render_video_task(project_id)

    # タスクラッパー自身のセッションはrollback済み。別セッションで検証する。
    verify_session = file_db_session_factory()
    try:
        job_run = (
            verify_session.query(JobRun)
            .filter(JobRun.entity_id == project_id, JobRun.job_type == "render_video")
            .one()
        )
        assert job_run.status == "failed"
        assert job_run.last_error is not None

        refreshed = verify_session.get(VideoProject, project_id)
        assert refreshed.status == "RENDER_FAILED"
    finally:
        verify_session.close()


def _make_upload_ready_project(session, tmp_path: Path) -> VideoProject:
    channel = Channel(name="ch")
    session.add(channel)
    session.flush()

    topic = Topic(channel_id=channel.id, title="topic", source_type="manual", source_ref="r-1")
    session.add(topic)
    session.flush()

    script = Script(
        topic_id=topic.id,
        version=1,
        title="タイトル",
        body={"description": "説明文", "tags": ["tag1"]},
        source_manifest={},
        status="reviewed",
    )
    session.add(script)
    session.flush()

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
    session.add(project)
    session.flush()

    for reviewer_type in ("machine", "content"):
        session.add(
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
    session.add(Approval(video_project_id=project.id, decision="approved", decided_by="tester"))
    session.flush()
    return project


def test_upload_video_task_persists_job_failure_and_upload_failed_after_rollback(
    file_db_session_factory: sessionmaker,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_session = file_db_session_factory()
    project = _make_upload_ready_project(setup_session, tmp_path)
    project_id = project.id
    setup_session.commit()
    setup_session.close()

    class _FailingProvider:
        async def check_auth(self) -> bool:
            return True

        async def list_recent_uploads(self, *, max_results: int) -> list:
            return []

        async def upload_video(self, *, request: UploadRequest) -> UploadResult:
            raise RuntimeError("simulated upload failure")

    monkeypatch.setattr(publishing_tasks, "get_youtube_provider", lambda: _FailingProvider())

    with pytest.raises(RuntimeError, match="simulated upload failure"):
        publishing_tasks.upload_video_task(project_id)

    verify_session = file_db_session_factory()
    try:
        job_run = (
            verify_session.query(JobRun)
            .filter(JobRun.entity_id == project_id, JobRun.job_type == "upload_video")
            .one()
        )
        assert job_run.status == "failed"
        assert job_run.last_error is not None

        refreshed = verify_session.get(VideoProject, project_id)
        assert refreshed.status == "UPLOAD_FAILED"

        publication = (
            verify_session.query(Publication)
            .filter(Publication.video_project_id == project_id)
            .one()
        )
        assert publication.upload_status == "failed"
        assert publication.last_error is not None
        assert "simulated upload failure" in publication.last_error
    finally:
        verify_session.close()
