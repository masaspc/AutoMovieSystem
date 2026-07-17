"""トレンド記事のワンクリック動画化(D-026)。

Topic(source_type="trend")+Evidence(出典URL)+ニュース解説Short設定の
VideoProjectを作成し、既存の一括制作(produce_video_task)へ引き渡せる状態にする。
同一URLの二重動画化はsource_refで防止する。
"""

from __future__ import annotations

import hashlib
from urllib.parse import urlsplit

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import get_logger
from app.models.channel import Channel
from app.models.evidence import Evidence
from app.models.topic import Topic
from app.providers.trends.base import (
    TREND_SOURCE_MAX_CHARS,
    TREND_SUMMARY_MAX_CHARS,
    TREND_TITLE_MAX_CHARS,
    normalize_trend_url,
    truncate_trend_text,
)
from app.schemas.production_settings import ProductionSettings
from app.services.channels.policy import get_editorial_policy
from app.services.orchestration import (
    _advance_status,  # noqa: SLF001 - オーケストレーションの該当ステップを再利用する
    _get_or_create_video_project,  # noqa: SLF001
)

logger = get_logger(__name__)

# トレンド動画の既定設定: スピード重視のニュース解説Short。
TREND_PRODUCTION_SETTINGS = ProductionSettings(
    preset="short", script_template="news_commentary", bgm_mood="serious"
)


def resolve_trend_feed_urls(channel: Channel | None, settings: Settings) -> list[str]:
    """チャンネル固有フィードを優先し、空ならグローバル設定へ戻す。"""
    policy_urls = get_editorial_policy(channel).trend_feed_urls
    if policy_urls:
        return policy_urls
    return [url.strip() for url in settings.TREND_FEED_URLS.split(",") if url.strip()]


def settings_for_channel_trends(channel: Channel | None, settings: Settings) -> Settings:
    """既存Providerへ解決済みフィードを渡すための設定コピーを作る。"""
    feed_urls = resolve_trend_feed_urls(channel, settings)
    return settings.model_copy(update={"TREND_FEED_URLS": ",".join(feed_urls)})


def trend_source_ref(url: str) -> str:
    """URLから決定的な重複防止キーを作る。"""
    normalized_url = normalize_trend_url(url)
    return "trend:" + hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:16]


def _get_existing_trend_topic(
    session: Session, *, channel_id: str, source_ref: str
) -> Topic | None:
    return (
        session.query(Topic)
        .filter(
            Topic.channel_id == channel_id,
            Topic.source_type == "trend",
            Topic.source_ref == source_ref,
        )
        .one_or_none()
    )


def instant_videoize(
    session: Session,
    *,
    channel_id: str,
    title: str,
    url: str,
    summary: str,
    source: str,
) -> Topic:
    """トレンド記事からTopic+Evidence+設定済みVideoProjectを作成する(URL単位で冪等)。"""
    channel = session.get(Channel, channel_id)
    if channel is None:
        raise ValueError(f"Channel not found: {channel_id}")

    normalized_url = normalize_trend_url(url)
    normalized_title = truncate_trend_text(title, max_chars=TREND_TITLE_MAX_CHARS)
    if not normalized_title:
        raise ValueError("トレンド記事のタイトルが空です")
    normalized_source = truncate_trend_text(source, max_chars=TREND_SOURCE_MAX_CHARS)
    if not normalized_source:
        normalized_source = urlsplit(normalized_url).hostname or "出典不明"
    claim = truncate_trend_text(
        summary.strip() or normalized_title, max_chars=TREND_SUMMARY_MAX_CHARS
    )

    source_ref = trend_source_ref(normalized_url)
    existing = _get_existing_trend_topic(session, channel_id=channel_id, source_ref=source_ref)
    if existing is not None:
        return existing

    topic = Topic(
        channel_id=channel_id,
        title=normalized_title,
        description=f"出典: {normalized_source} {normalized_url}",
        source_type="trend",
        source_url=normalized_url,
        source_ref=source_ref,
    )
    try:
        with session.begin_nested():
            session.add(topic)
            session.flush()
    except IntegrityError:
        # 同じ記事への並行クリックで他トランザクションが先に作成した場合は勝者を返す。
        if topic in session:
            session.expunge(topic)
        winner = _get_existing_trend_topic(session, channel_id=channel_id, source_ref=source_ref)
        if winner is None:  # pragma: no cover - UNIQUE以外のIntegrityErrorは再送出
            raise
        return winner

    session.add(
        Evidence(
            topic_id=topic.id,
            source_url=normalized_url,
            source_title=f"{normalized_source}: {normalized_title}"[:512],
            publisher=normalized_source,
            claim=claim,
            excerpt_hash=hashlib.sha256(claim.encode("utf-8")).hexdigest(),
            verification_status="pending",
        )
    )

    project = _get_or_create_video_project(session, topic_id=topic.id)
    policy_defaults = get_editorial_policy(channel).default_production_settings
    try:
        trend_settings = ProductionSettings.model_validate(
            {**TREND_PRODUCTION_SETTINGS.model_dump(), **policy_defaults}
        )
    except ValueError:
        logger.warning("channel_default_production_settings_invalid", channel_id=channel.id)
        trend_settings = TREND_PRODUCTION_SETTINGS
    project.production_settings = trend_settings.model_dump()
    _advance_status(project, "TOPIC_SCORED")
    _advance_status(project, "RESEARCH_READY")
    session.flush()

    logger.info("trend_topic_created", topic_id=topic.id, url=normalized_url)
    return topic
