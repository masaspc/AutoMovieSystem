"""投稿後セルフレビューのCeleryタスク。

手動実行はPublication 1件を処理する。日次実行はUTCの前日分の指標がある
Publicationを対象にし、1件の失敗で残りを止めない。
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from app.core.logging import get_logger
from app.core.timeutil import utcnow_naive
from app.db.session import SessionLocal
from app.models.video_metric_daily import VideoMetricDaily
from app.providers.llm.base import LLMProvider
from app.providers.llm.factory import get_llm_provider
from app.services.feedback.self_review import run_self_review
from app.services.jobs import record_failure_in_new_session
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


def _persist_failure(
    publication_id: str, exc: Exception, *, metric_date: date | None = None
) -> None:
    """元トランザクションのrollback後にJobRun失敗を別セッションで残す。"""
    idempotency_key = getattr(exc, "idempotency_key", None)
    if (not isinstance(idempotency_key, str) or not idempotency_key) and metric_date is not None:
        idempotency_key = f"self_review:{publication_id}:{metric_date.isoformat()}"
    if not isinstance(idempotency_key, str) or not idempotency_key:
        return
    try:
        record_failure_in_new_session(
            idempotency_key=idempotency_key,
            job_type="self_review",
            entity_type="publication",
            entity_id=publication_id,
            error=exc,
        )
    except Exception as record_exc:  # noqa: BLE001 - 日次バッチは残りの投稿を継続する
        logger.error(
            "self_review_failure_record_failed",
            publication_id=publication_id,
            error_type=type(record_exc).__name__,
        )


@celery_app.task(name="feedback.run_self_review")
def run_self_review_task(publication_id: str) -> int:
    """Publication 1件をレビューし、対象指標日のInsight件数を返す。"""
    session = SessionLocal()
    try:
        insights = asyncio.run(
            run_self_review(
                session,
                publication_id=publication_id,
                provider=get_llm_provider(),
            )
        )
        session.commit()
        return len(insights)
    except Exception as exc:
        session.rollback()
        _persist_failure(publication_id, exc)
        raise
    finally:
        session.close()


@celery_app.task(name="feedback.run_daily_self_reviews")
def run_daily_self_reviews() -> dict[str, str | int]:
    """UTC前日分の指標がある投稿を、投稿単位でfail-softにレビューする。

    戻り値の ``successful_attempts`` は、新規生成・冪等skip・レッスン0件を問わず、
    例外なく処理を完了したPublication数。``insights_returned`` はサービスから返った
    Insight件数の合計であり、新規作成件数とは限らない。
    """
    target_date = (utcnow_naive() - timedelta(days=1)).date()

    discovery_session = SessionLocal()
    try:
        publication_ids = [
            publication_id
            for (publication_id,) in (
                discovery_session.query(VideoMetricDaily.publication_id)
                .filter(VideoMetricDaily.metric_date == target_date)
                .distinct()
                .order_by(VideoMetricDaily.publication_id.asc())
                .all()
            )
        ]
    finally:
        discovery_session.close()

    successful_attempts = 0
    failed_attempts = 0
    insights_returned = 0
    provider: LLMProvider | None = None

    for publication_id in publication_ids:
        session = SessionLocal()
        try:
            if provider is None:
                provider = get_llm_provider()
            insights = asyncio.run(
                run_self_review(
                    session,
                    publication_id=publication_id,
                    provider=provider,
                    metric_date=target_date,
                )
            )
            session.commit()
            successful_attempts += 1
            insights_returned += len(insights)
        except Exception as exc:  # noqa: BLE001 - 投稿単位で記録し、残りを継続する
            session.rollback()
            failed_attempts += 1
            _persist_failure(publication_id, exc, metric_date=target_date)
            logger.warning(
                "daily_self_review_publication_failed",
                publication_id=publication_id,
                metric_date=target_date.isoformat(),
                error_type=type(exc).__name__,
            )
        finally:
            session.close()

    summary: dict[str, str | int] = {
        "metric_date": target_date.isoformat(),
        "candidate_publications": len(publication_ids),
        "successful_attempts": successful_attempts,
        "failed_attempts": failed_attempts,
        "insights_returned": insights_returned,
    }
    logger.info("daily_self_reviews_completed", **summary)
    return summary
