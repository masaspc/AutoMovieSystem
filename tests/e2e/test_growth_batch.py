"""量産バッチ(グロース機能)のE2Eテスト。

SQLiteファイルDB + 全Fakeプロバイダーで `run_production_batch` を実行し、
スコア上位の未制作企画がまとめて自動レビュー通過まで制作されること、
公開(承認・アップロード)には進まないこと、再実行で重複しないことを検証する。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401  metadataにモデルを登録するため import
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import enable_sqlite_foreign_keys
from app.models.approval import Approval
from app.models.channel import Channel
from app.models.publication import Publication
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.providers.llm.fake import DeterministicFakeLLMProvider
from app.providers.tts.fake import FakeTTSProvider
from app.providers.youtube.fake import FakeYouTubeProvider
from app.services.growth import run_production_batch
from app.services.orchestration import PipelineProviders

pytestmark = pytest.mark.e2e


@pytest.fixture
def e2e_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "growth-e2e.db"
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


def _seed_topics(session: Session, count: int) -> Channel:
    channel = Channel(name="量産テストチャンネル", default_privacy_status="private")
    session.add(channel)
    session.flush()
    for index in range(count):
        session.add(
            Topic(
                channel_id=channel.id,
                title=f"量産テスト企画{index + 1}: 開発ツール比較",
                description="量産バッチE2E用の企画です。",
                source_type="manual",
                source_ref=f"batch-topic-{index + 1}",
                total_score=float(10 - index),
            )
        )
    session.flush()
    session.commit()
    return channel


def _providers() -> PipelineProviders:
    return PipelineProviders(
        llm=DeterministicFakeLLMProvider(),
        tts=FakeTTSProvider(),
        youtube=FakeYouTubeProvider(),
    )


def test_production_batch_produces_reviewed_videos_without_publishing(
    e2e_session: Session,
) -> None:
    session = e2e_session
    channel = _seed_topics(session, count=3)

    report = asyncio.run(
        run_production_batch(session, channel_id=channel.id, providers=_providers(), limit=2)
    )
    session.commit()

    # limit=2 のためスコア上位2件だけ制作される
    assert report.attempted == 2
    assert report.review_passed == 2
    assert report.errors == []

    projects = session.query(VideoProject).all()
    assert len(projects) == 2
    assert all(p.status == "AUTOMATED_REVIEW_PASSED" for p in projects)
    assert all(p.output_path and p.checksum for p in projects)

    # fail-closed: 承認・アップロードには進まない
    assert session.query(Approval).count() == 0
    assert session.query(Publication).count() == 0


def test_production_batch_rerun_does_not_duplicate(e2e_session: Session) -> None:
    session = e2e_session
    channel = _seed_topics(session, count=2)
    providers = _providers()

    first = asyncio.run(
        run_production_batch(session, channel_id=channel.id, providers=providers, limit=5)
    )
    session.commit()
    assert first.attempted == 2

    # 再実行: 制作済み(AUTOMATED_REVIEW_PASSED)はバッチ対象から外れ、重複制作されない
    second = asyncio.run(
        run_production_batch(session, channel_id=channel.id, providers=providers, limit=5)
    )
    session.commit()

    assert second.attempted == 0
    assert session.query(VideoProject).count() == 2
