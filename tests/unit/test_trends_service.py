"""トレンド即動画化サービスの検証(FakeのみでネットワークもLLMも不使用)。"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.channel import Channel
from app.models.evidence import VERIFICATION_STATUSES, Evidence
from app.models.topic import SOURCE_TYPES, Topic
from app.models.video_project import VideoProject
from app.schemas.production_settings import ProductionSettings
from app.services.orchestration import _ensure_dummy_evidence
from app.services.trends.service import (
    instant_videoize,
    resolve_trend_feed_urls,
    trend_source_ref,
)


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
    assert topic.source_url == "https://example.com/news/1"

    evidence = db_session.query(Evidence).filter(Evidence.topic_id == topic.id).one()
    assert evidence.source_url == "https://example.com/news/1"
    assert evidence.verification_status == "pending"

    project = db_session.query(VideoProject).filter(VideoProject.topic_id == topic.id).one()
    assert project.status == "RESEARCH_READY"
    settings = ProductionSettings.model_validate(project.production_settings)
    assert settings.preset == "short"
    assert settings.script_template == "news_commentary"
    assert settings.bgm_mood == "serious"
    assert "trend" in SOURCE_TYPES
    assert "pending" in VERIFICATION_STATUSES


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


def test_instant_videoize_enforces_storage_boundaries(db_session: Session) -> None:
    channel = _make_channel(db_session)

    topic = instant_videoize(
        db_session,
        channel_id=channel.id,
        title="題" * 400,
        url="  https://example.com/news/long  ",
        summary="要" * 400,
        source="配信元" * 100,
    )
    db_session.commit()

    evidence = db_session.query(Evidence).filter(Evidence.topic_id == topic.id).one()
    assert len(topic.title) == 255
    assert topic.title.endswith("…")
    assert topic.source_url == "https://example.com/news/long"
    assert len(evidence.claim) == 200
    assert evidence.claim.endswith("…")
    assert evidence.publisher is not None and len(evidence.publisher) == 255


def test_instant_videoize_uses_title_when_summary_is_blank(db_session: Session) -> None:
    channel = _make_channel(db_session)

    topic = instant_videoize(
        db_session,
        channel_id=channel.id,
        title="要約なしの記事",
        url="https://example.com/news/no-summary",
        summary="   ",
        source="News",
    )

    evidence = db_session.query(Evidence).filter(Evidence.topic_id == topic.id).one()
    assert evidence.claim == "要約なしの記事"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "https://"])
def test_instant_videoize_rejects_non_http_article_url(db_session: Session, url: str) -> None:
    channel = _make_channel(db_session)

    with pytest.raises(ValueError, match=r"http\(s\)"):
        instant_videoize(
            db_session,
            channel_id=channel.id,
            title="記事",
            url=url,
            summary="要約",
            source="News",
        )


def test_instant_videoize_uses_full_topic_natural_key(db_session: Session) -> None:
    channel = _make_channel(db_session)
    url = "https://example.com/same-ref"
    source_ref = trend_source_ref(url)
    manual = Topic(
        channel_id=channel.id,
        title="手動企画",
        source_type="manual",
        source_ref=source_ref,
    )
    db_session.add(manual)
    db_session.flush()

    trend = instant_videoize(
        db_session,
        channel_id=channel.id,
        title="トレンド企画",
        url=url,
        summary="要約",
        source="News",
    )
    db_session.commit()

    assert trend.source_type == "trend"
    assert trend.id != manual.id
    assert db_session.query(Topic).filter(Topic.source_ref == source_ref).count() == 2


def test_instant_videoize_rejects_missing_channel(db_session: Session) -> None:
    with pytest.raises(ValueError, match="Channel not found"):
        instant_videoize(
            db_session,
            channel_id="missing",
            title="記事",
            url="https://example.com/article",
            summary="要約",
            source="News",
        )


def test_existing_trend_evidence_prevents_dummy_evidence(db_session: Session) -> None:
    channel = _make_channel(db_session)
    topic = instant_videoize(
        db_session,
        channel_id=channel.id,
        title="記事",
        url="https://example.com/real-evidence",
        summary="実際の記事要約",
        source="News",
    )
    existing = db_session.query(Evidence).filter(Evidence.topic_id == topic.id).one()

    selected = _ensure_dummy_evidence(db_session, topic_id=topic.id)

    assert selected.id == existing.id
    assert db_session.query(Evidence).filter(Evidence.topic_id == topic.id).count() == 1


def test_channel_trend_feeds_override_global_and_empty_policy_falls_back() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        TREND_FEED_URLS="https://global.example/rss,https://global.example/second",
    )
    channel = Channel(
        name="finance",
        editorial_policy={"trend_feed_urls": ["https://channel.example/rss"]},
    )
    assert resolve_trend_feed_urls(channel, settings) == ["https://channel.example/rss"]

    channel.editorial_policy = {"trend_feed_urls": []}
    assert resolve_trend_feed_urls(channel, settings) == [
        "https://global.example/rss",
        "https://global.example/second",
    ]
