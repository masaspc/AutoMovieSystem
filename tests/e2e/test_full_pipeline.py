"""企画->Insightまでの全工程E2Eテスト(Phase 7B)。

SQLiteファイルDB(tmp_path)+ 全Fakeプロバイダー + Celery eager で
`app.services.orchestration.run_full_pipeline` を実行し、以下を検証する:

1. 1回実行で全工程が完走すること。
2. 同一入力で2回実行しても重複レコードが作られないこと(冪等性)。
3. レンダリング失敗からの復旧(RENDER_FAILED -> 修復後の再実行で完走)。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401  metadataにモデルを登録するため import
import app.services.media.renderer as renderer_module
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import enable_sqlite_foreign_keys
from app.models.approval import Approval
from app.models.channel import Channel
from app.models.comment import Comment
from app.models.insight import Insight
from app.models.publication import Publication
from app.models.review import Review
from app.models.script import Script
from app.models.topic import Topic
from app.models.usage_record import UsageRecord
from app.models.video_metric_daily import VideoMetricDaily
from app.models.video_project import VideoProject
from app.providers.llm.fake import DeterministicFakeLLMProvider
from app.providers.tts.fake import FakeTTSProvider
from app.providers.youtube.fake import FakeYouTubeProvider
from app.services.media.probe import probe_video
from app.services.orchestration import PipelineProviders, run_full_pipeline
from app.services.state_machine import NORMAL_STATUSES

pytestmark = pytest.mark.e2e


@pytest.fixture
def e2e_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """SQLiteファイルDB + 隔離された生成物ディレクトリのセッションを提供する。"""
    db_path = tmp_path / "e2e.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("GENERATED_DIR", str(tmp_path / "generated"))
    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "true")
    get_settings.cache_clear()

    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        get_settings.cache_clear()


def _make_channel_and_topic(session: Session, *, source_ref: str) -> tuple[Channel, Topic]:
    channel = Channel(name="E2Eテストチャンネル", default_privacy_status="private")
    session.add(channel)
    session.flush()

    topic = Topic(
        channel_id=channel.id,
        title="E2Eテスト企画: Pythonの新機能まとめ",
        description="E2Eテスト用の企画説明文です。",
        source_type="manual",
        source_ref=source_ref,
    )
    session.add(topic)
    session.flush()
    session.commit()
    return channel, topic


def _build_providers() -> PipelineProviders:
    return PipelineProviders(
        llm=DeterministicFakeLLMProvider(),
        tts=FakeTTSProvider(),
        youtube=FakeYouTubeProvider(),
    )


def test_run_full_pipeline_end_to_end(e2e_session: Session) -> None:
    """1回の実行で企画->Insight/派生Topicまで全工程が完走すること。"""
    session = e2e_session
    channel, topic = _make_channel_and_topic(session, source_ref="e2e-topic-1")
    providers = _build_providers()

    report = asyncio.run(
        run_full_pipeline(session, channel_id=channel.id, topic_id=topic.id, providers=providers)
    )
    session.commit()

    project = session.get(VideoProject, report.video_project_id)
    assert project is not None
    assert NORMAL_STATUSES.index(project.status) >= NORMAL_STATUSES.index("UPLOADED_PRIVATE")

    assert project.output_path is not None
    output_path = Path(project.output_path)
    assert output_path.exists()
    assert output_path.stat().st_size > 0
    probe_result = probe_video(output_path)
    assert probe_result.duration_seconds > 0

    reviews = session.query(Review).filter(Review.video_project_id == project.id).all()
    assert len(reviews) == 2
    assert all(r.passed for r in reviews)

    approvals = session.query(Approval).filter(Approval.video_project_id == project.id).all()
    assert len(approvals) == 1

    publications = (
        session.query(Publication).filter(Publication.video_project_id == project.id).all()
    )
    assert len(publications) == 1
    assert publications[0].youtube_video_id is not None

    metrics = (
        session.query(VideoMetricDaily)
        .filter(VideoMetricDaily.publication_id == publications[0].id)
        .all()
    )
    assert len(metrics) >= 1

    comments = session.query(Comment).filter(Comment.publication_id == publications[0].id).all()
    assert len(comments) >= 1
    assert all(c.category for c in comments)

    insights = session.query(Insight).all()
    assert len(insights) >= 1

    assert len(report.derived_topic_ids) >= 1

    usage_records = session.query(UsageRecord).all()
    assert len(usage_records) >= 1


def test_run_full_pipeline_is_idempotent_on_rerun(e2e_session: Session) -> None:
    """同一入力で2回実行しても重複レコードが作られないこと(冪等性)。"""
    session = e2e_session
    channel, topic = _make_channel_and_topic(session, source_ref="e2e-topic-2")
    providers = _build_providers()

    report1 = asyncio.run(
        run_full_pipeline(session, channel_id=channel.id, topic_id=topic.id, providers=providers)
    )
    session.commit()

    video_project_count_after_1 = session.query(VideoProject).count()
    script_count_after_1 = session.query(Script).filter(Script.topic_id == topic.id).count()
    publication_count_after_1 = session.query(Publication).count()
    comment_count_after_1 = session.query(Comment).count()
    usage_record_count_after_1 = session.query(UsageRecord).count()

    report2 = asyncio.run(
        run_full_pipeline(session, channel_id=channel.id, topic_id=topic.id, providers=providers)
    )
    session.commit()

    assert report1.video_project_id == report2.video_project_id
    assert report1.script_id == report2.script_id
    assert report1.publication_id == report2.publication_id
    assert report1.youtube_video_id == report2.youtube_video_id

    assert session.query(VideoProject).count() == video_project_count_after_1
    assert session.query(Script).filter(Script.topic_id == topic.id).count() == script_count_after_1
    assert session.query(Publication).count() == publication_count_after_1
    assert session.query(Comment).count() == comment_count_after_1
    assert session.query(UsageRecord).count() == usage_record_count_after_1

    assert isinstance(providers.youtube, FakeYouTubeProvider)
    assert len(providers.youtube.store.videos) == 1


def test_run_full_pipeline_recovers_from_render_failure(
    e2e_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """レンダリングを一度失敗させ RENDER_FAILED を確認し、修復後の再実行で完走すること。"""
    session = e2e_session
    channel, topic = _make_channel_and_topic(session, source_ref="e2e-topic-3")
    providers = _build_providers()

    original_render_video = renderer_module.render_video
    call_count = {"n": 0}

    def _flaky_render_video(inputs, *, settings=None):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise renderer_module.RenderError("simulated ffmpeg failure for recovery test")
        return original_render_video(inputs, settings=settings)

    monkeypatch.setattr(renderer_module, "render_video", _flaky_render_video)

    with pytest.raises(Exception):  # noqa: B017 - PipelineRenderErrorの伝播を許容
        asyncio.run(
            run_full_pipeline(
                session, channel_id=channel.id, topic_id=topic.id, providers=providers
            )
        )
    # サービス層はflushのみ(D-017)。呼び出し元として失敗状態を確定させる。
    session.commit()

    project = (
        session.query(VideoProject)
        .filter(VideoProject.topic_id == topic.id, VideoProject.generation == 1)
        .one()
    )
    assert project.status == "RENDER_FAILED"

    report = asyncio.run(
        run_full_pipeline(session, channel_id=channel.id, topic_id=topic.id, providers=providers)
    )
    session.commit()

    project = session.get(VideoProject, report.video_project_id)
    assert project is not None
    assert NORMAL_STATUSES.index(project.status) >= NORMAL_STATUSES.index("UPLOADED_PRIVATE")
    assert call_count["n"] == 2
