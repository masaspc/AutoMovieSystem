"""自動レビューCeleryタスク(薄いラッパー。ロジックは app.services.reviews.service)。"""

from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.providers.llm.factory import get_llm_provider
from app.services.reviews.service import run_automated_review
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
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
