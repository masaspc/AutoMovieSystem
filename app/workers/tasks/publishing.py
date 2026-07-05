"""アップロード・公開予約Celeryタスク(薄いラッパー。ロジックは app.services.publishing)。"""

from __future__ import annotations

import asyncio
from datetime import datetime

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.providers.youtube.factory import get_youtube_provider
from app.services.jobs import record_failure_in_new_session
from app.services.publishing.scheduler import schedule_publication
from app.services.publishing.uploader import (
    record_publication_failure_in_new_session,
    upload_video,
)
from app.services.state_machine import apply_failure_transition_in_new_session
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
    except Exception as exc:
        session.rollback()
        idempotency_key = getattr(exc, "idempotency_key", None)
        if idempotency_key is not None:
            record_failure_in_new_session(
                idempotency_key=idempotency_key,
                job_type="upload_video",
                entity_type="video_project",
                entity_id=video_project_id,
                error=exc,
            )
            # D-017: サービス層内のUPLOAD_FAILED遷移はflushのみでrollbackにより消えて
            # いるため、新規セッションで再適用する(遷移元状態が合致する場合のみ)。
            apply_failure_transition_in_new_session(
                video_project_id=video_project_id, to_state="UPLOAD_FAILED"
            )
        publication_idempotency_key = getattr(exc, "publication_idempotency_key", None)
        if publication_idempotency_key is not None:
            record_publication_failure_in_new_session(
                video_project_id=video_project_id,
                idempotency_key=publication_idempotency_key,
                error=exc,
            )
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
