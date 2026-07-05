"""アップロード・公開予約Celeryタスク(薄いラッパー。ロジックは app.services.publishing)。"""

from __future__ import annotations

import asyncio
from datetime import datetime

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.providers.youtube.factory import get_youtube_provider
from app.services.publishing.scheduler import schedule_publication
from app.services.publishing.uploader import upload_video
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="publishing.upload_video")
def upload_video_task(video_project_id: str) -> str:
    """VideoProjectをYouTubeへアップロードする(冪等。ADR-0005 reconcile対応)。"""
    session = SessionLocal()
    try:
        provider = get_youtube_provider()
        publication = asyncio.run(
            upload_video(session, video_project_id=video_project_id, provider=provider)
        )
        session.commit()
        return publication.id
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(name="publishing.schedule_video")
def schedule_video_task(publication_id: str, publish_at_iso: str) -> bool:
    """公開ゲートを満たす場合のみ予約公開する(満たさない場合はFalseを返し例外にしない)。"""
    session = SessionLocal()
    try:
        provider = get_youtube_provider()
        publish_at = datetime.fromisoformat(publish_at_iso)
        result = asyncio.run(
            schedule_publication(
                session,
                publication_id=publication_id,
                publish_at=publish_at,
                provider=provider,
            )
        )
        session.commit()
        return result.scheduled
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
