"""トレンド記事のワンクリック動画化(D-022)。

Topic(source_type="trend")+Evidence(出典URL)+ニュース解説Short設定の
VideoProjectを作成し、既存の一括制作(produce_video_task)へ引き渡せる状態にする。
同一URLの二重動画化はsource_refで防止する。
"""

from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.evidence import Evidence
from app.models.topic import Topic
from app.schemas.production_settings import ProductionSettings
from app.services.orchestration import (
    _advance_status,  # noqa: SLF001 - オーケストレーションの該当ステップを再利用する
    _get_or_create_video_project,  # noqa: SLF001
)

logger = get_logger(__name__)

# トレンド動画の既定設定: スピード重視のニュース解説Short。
TREND_PRODUCTION_SETTINGS = ProductionSettings(
    preset="short", script_template="news_commentary", bgm_mood="serious"
)


def trend_source_ref(url: str) -> str:
    """URLから決定的な重複防止キーを作る。"""
    return "trend:" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


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
    source_ref = trend_source_ref(url)
    existing = (
        session.query(Topic)
        .filter(Topic.channel_id == channel_id, Topic.source_ref == source_ref)
        .one_or_none()
    )
    if existing is not None:
        return existing

    topic = Topic(
        channel_id=channel_id,
        title=title,
        description=f"出典: {source} {url}",
        source_type="trend",
        source_ref=source_ref,
    )
    session.add(topic)
    session.flush()

    claim = summary or title
    session.add(
        Evidence(
            topic_id=topic.id,
            source_url=url,
            source_title=f"{source}: {title}"[:255],
            publisher=source[:255],
            claim=claim,
            excerpt_hash=hashlib.sha256(claim.encode("utf-8")).hexdigest(),
            verification_status="pending",
        )
    )

    project = _get_or_create_video_project(session, topic_id=topic.id)
    project.production_settings = TREND_PRODUCTION_SETTINGS.model_dump()
    _advance_status(project, "TOPIC_SCORED")
    _advance_status(project, "RESEARCH_READY")
    session.flush()

    logger.info("trend_topic_created", topic_id=topic.id, url=url)
    return topic
