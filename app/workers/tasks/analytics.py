"""分析・コメント・Insight同期Celeryタスク(Phase 6)。"""

from __future__ import annotations

import asyncio

from app.db.session import SessionLocal
from app.providers.youtube.factory import get_youtube_provider
from app.services.analytics.sync import sync_video_metrics
from app.services.comments.sync import sync_comments
from app.services.feedback.insights import generate_publication_insights
from app.services.feedback.sync import (
    sync_all_completed_publications_feedback,
    sync_publication_feedback,
)
from app.services.publishing.scheduler import finalize_due_publications
from app.workers.celery_app import celery_app


@celery_app.task(name="analytics.sync_metrics")
def sync_metrics_task(publication_id: str) -> str:
    """Publicationの基本統計を同期し、VideoMetricDaily.idを返す。"""
    session = SessionLocal()
    try:
        provider = get_youtube_provider()
        metric = asyncio.run(
            sync_video_metrics(session, publication_id=publication_id, provider=provider)
        )
        session.commit()
        return metric.id
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(name="analytics.sync_comments")
def sync_comments_task(publication_id: str) -> int:
    """Publicationのコメントを差分同期・分類し、今回取得した件数を返す。"""
    session = SessionLocal()
    try:
        provider = get_youtube_provider()
        comments = asyncio.run(
            sync_comments(session, publication_id=publication_id, provider=provider)
        )
        session.commit()
        return len(comments)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(name="analytics.generate_insights")
def generate_insights_task(publication_id: str) -> int:
    """保存済みの指標・コメント分類結果からInsightを生成し、件数を返す。"""
    session = SessionLocal()
    try:
        insights = generate_publication_insights(session, publication_id=publication_id)
        session.commit()
        return len(insights)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(name="analytics.sync_feedback")
def sync_feedback_task(publication_id: str) -> dict[str, int | str]:
    """PublicationのPhase 6同期を一括実行する。"""
    session = SessionLocal()
    try:
        provider = get_youtube_provider()
        result = asyncio.run(
            sync_publication_feedback(session, publication_id=publication_id, provider=provider)
        )
        session.commit()
        return {
            "metric_id": result.metric.id,
            "comments_synced": len(result.comments),
            "insights_generated": len(result.insights),
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(name="analytics.sync_completed_feedback")
def sync_completed_feedback_task() -> int:
    """アップロード完了済みPublicationをまとめてPhase 6同期する。

    先頭で公開期日到来分の確定処理(修正5)を行ってから同期する。
    """
    session = SessionLocal()
    try:
        finalize_due_publications(session)
        session.commit()

        provider = get_youtube_provider()
        results = asyncio.run(sync_all_completed_publications_feedback(session, provider=provider))
        session.commit()
        return len(results)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(name="analytics.finalize_due_publications")
def finalize_due_publications_task() -> int:
    """公開期日到来のPublicationを確定させる単独タスク(修正5・15分毎beat)。"""
    session = SessionLocal()
    try:
        finalized_count = finalize_due_publications(session)
        session.commit()
        return finalized_count
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
