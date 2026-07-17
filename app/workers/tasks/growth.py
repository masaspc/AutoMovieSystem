"""明示オプトインのグロース自動運転Celeryタスク。

トレンド候補の収集から自動レビューまでを日次で仕込み、人間にはprivate
アップロード承認だけを残す。公開・公開予約はこのタスクの責務に含めない。
"""

from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.channel import Channel
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.providers.trends.factory import get_trend_provider
from app.services.trends.service import (
    instant_videoize,
    settings_for_channel_trends,
    trend_source_ref,
)
from app.workers.celery_app import celery_app
from app.workers.tasks.production import produce_video_task

logger = get_logger(__name__)

_RESUMABLE_PROJECT_STATUSES = {
    "TOPIC_CREATED",
    "TOPIC_SCORED",
    "RESEARCH_READY",
    "SCRIPT_GENERATED",
    "SCRIPT_REVIEWED",
}


def _skipped(reason: str) -> dict[str, object]:
    return {
        "status": "skipped",
        "reason": reason,
        "fetched": 0,
        "selected": 0,
        "created": 0,
        "dispatched": 0,
        "failed": 0,
    }


@celery_app.task(name="growth.run_daily_autopilot")
def run_daily_autopilot() -> dict[str, object]:
    """最新トレンドを最大3件、URL冪等で制作キューへ送る。

    設定・チャンネルをfail-closedで検証する。記事ごとに独立してcommit/rollback
    するため、1件の不正データやDBエラーで取得済みの他候補を巻き添えにしない。
    """
    settings = get_settings()
    if not settings.GROWTH_AUTOPILOT_ENABLED:
        return _skipped("disabled")

    channel_id = settings.GROWTH_AUTOPILOT_CHANNEL_ID.strip()
    if not channel_id:
        logger.warning("growth_autopilot_channel_not_configured")
        return _skipped("channel_not_configured")

    validation_session = SessionLocal()
    try:
        channel = validation_session.get(Channel, channel_id)
        if channel is None:
            logger.warning("growth_autopilot_channel_not_found", channel_id=channel_id)
            return _skipped("channel_not_found")
        provider_settings = settings_for_channel_trends(channel, settings)
    finally:
        validation_session.close()

    fetch_limit = max(settings.GROWTH_AUTOPILOT_DAILY_LIMIT, settings.TREND_FETCH_LIMIT)
    try:
        provider = get_trend_provider(provider_settings)
        items = asyncio.run(provider.fetch_latest(limit=fetch_limit))
    except Exception as exc:  # noqa: BLE001 - beatを停止せず次回収集へ回す
        logger.warning(
            "growth_autopilot_fetch_failed",
            error_type=type(exc).__name__,
        )
        return {
            "status": "completed_with_errors",
            "reason": "trend_fetch_failed",
            "fetched": 0,
            "selected": 0,
            "created": 0,
            "dispatched": 0,
            "failed": 1,
        }

    selected = 0
    created = 0
    dispatched = 0
    failed = 0
    already_finished = 0

    for item in items:
        if selected >= settings.GROWTH_AUTOPILOT_DAILY_LIMIT:
            break

        session = SessionLocal()
        topic_id: str | None = None
        try:
            # 実行中に対象チャンネルが削除されても、新しいTopicを孤児化させない。
            if session.get(Channel, channel_id) is None:
                raise ValueError("configured channel no longer exists")

            source_ref = trend_source_ref(item.url)
            existing = (
                session.query(Topic)
                .filter(
                    Topic.channel_id == channel_id,
                    Topic.source_type == "trend",
                    Topic.source_ref == source_ref,
                )
                .one_or_none()
            )
            topic = instant_videoize(
                session,
                channel_id=channel_id,
                title=item.title,
                url=item.url,
                summary=item.summary,
                source=item.source,
            )
            topic_id = topic.id
            project = (
                session.query(VideoProject)
                .filter(VideoProject.topic_id == topic.id)
                .order_by(VideoProject.generation.desc())
                .first()
            )
            if project is None:
                raise ValueError("trend topic has no video project")

            # 完了済み/レビュー待ち/失敗中の記事は日次上限を消費せず、次の新着を見る。
            if project.status not in _RESUMABLE_PROJECT_STATUSES:
                session.commit()
                already_finished += 1
                continue

            session.commit()
            selected += 1
            if existing is None:
                created += 1
        except Exception as exc:  # noqa: BLE001 - 記事単位のfail-soft
            session.rollback()
            failed += 1
            logger.warning(
                "growth_autopilot_item_failed",
                error_type=type(exc).__name__,
            )
            continue
        finally:
            session.close()

        assert topic_id is not None
        try:
            produce_video_task.delay(topic_id)
        except Exception as exc:  # noqa: BLE001 - Topicは次回の再収集で再開可能
            failed += 1
            logger.warning(
                "growth_autopilot_dispatch_failed",
                topic_id=topic_id,
                error_type=type(exc).__name__,
            )
            continue
        dispatched += 1
        logger.info("growth_autopilot_dispatched", topic_id=topic_id, channel_id=channel_id)

    status = "completed_with_errors" if failed else "completed"
    return {
        "status": status,
        "fetched": len(items),
        "selected": selected,
        "created": created,
        "dispatched": dispatched,
        "failed": failed,
        "already_finished": already_finished,
    }
