"""自動レビューCeleryタスク(薄いラッパー。ロジックは app.services.reviews.service)。"""

from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.providers.llm.factory import get_llm_provider
from app.services.jobs import record_failure_in_new_session
from app.services.reviews.service import run_automated_review
from app.services.state_machine import apply_failure_transition_in_new_session
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="reviews.run_automated_review")
def run_automated_review_task(video_project_id: str) -> str:
    """VIDEO_RENDEREDの動画を機械+コンテンツレビューする(冪等)。"""
    session = SessionLocal()
    try:
        provider = get_llm_provider()
        project = asyncio.run(
            run_automated_review(session, video_project_id=video_project_id, provider=provider)
        )
        session.commit()
        return project.id
    except Exception as exc:
        session.rollback()
        idempotency_key = getattr(exc, "idempotency_key", None)
        if idempotency_key is not None:
            record_failure_in_new_session(
                idempotency_key=idempotency_key,
                job_type="automated_review",
                entity_type="video_project",
                entity_id=video_project_id,
                error=exc,
            )
            # D-017: サービス層内のREVIEW_FAILED遷移はflushのみでrollbackにより消えて
            # いるため、新規セッションで再適用する(遷移元状態が合致する場合のみ)。
            apply_failure_transition_in_new_session(
                video_project_id=video_project_id, to_state="REVIEW_FAILED"
            )
        raise
    finally:
        session.close()
