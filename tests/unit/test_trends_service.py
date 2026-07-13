"""トレンド即動画化サービスの検証(FakeのみでネットワークもLLMも不使用)。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.evidence import Evidence
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.schemas.production_settings import ProductionSettings
from app.services.trends.service import instant_videoize, trend_source_ref


def _make_channel(db_session: Session) -> Channel:
    channel = Channel(name="ch")
    db_session.add(channel)
    db_session.flush()
    return channel


def test_instant_videoize_creates_topic_evidence_and_configured_project(
    db_session: Session,
) -> None:
    channel = _make_channel(db_session)

    topic = instant_videoize(
        db_session,
        channel_id=channel.id,
        title="新しい生成AIモデルが発表",
        url="https://example.com/news/1",
        summary="推論性能が強化された。",
        source="Example News",
    )
    db_session.commit()

    assert topic.source_type == "trend"
    assert topic.source_ref == trend_source_ref("https://example.com/news/1")

    evidence = db_session.query(Evidence).filter(Evidence.topic_id == topic.id).one()
    assert evidence.source_url == "https://example.com/news/1"
    assert evidence.verification_status == "pending"

    project = (
        db_session.query(VideoProject).filter(VideoProject.topic_id == topic.id).one()
    )
    assert project.status == "RESEARCH_READY"
    settings = ProductionSettings.model_validate(project.production_settings)
    assert settings.preset == "short"
    assert settings.script_template == "news_commentary"
    assert settings.bgm_mood == "serious"


def test_instant_videoize_dedups_same_url(db_session: Session) -> None:
    channel = _make_channel(db_session)
    first = instant_videoize(
        db_session,
        channel_id=channel.id,
        title="同じ記事",
        url="https://example.com/dup",
        summary="要約",
        source="News",
    )
    db_session.commit()
    second = instant_videoize(
        db_session,
        channel_id=channel.id,
        title="同じ記事(再押下)",
        url="https://example.com/dup",
        summary="要約",
        source="News",
    )
    db_session.commit()

    assert first.id == second.id
    assert db_session.query(Topic).filter(Topic.source_type == "trend").count() == 1
