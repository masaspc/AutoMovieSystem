"""シリーズカリキュラム生成Celeryタスク。"""

from __future__ import annotations

import asyncio

from app.db.session import SessionLocal
from app.providers.llm.factory import get_llm_provider
from app.services.series.service import generate_curriculum
from app.workers.celery_app import celery_app


@celery_app.task(name="series.generate_curriculum")
def generate_curriculum_task(series_id: str) -> int:
    session = SessionLocal()
    try:
        episodes = asyncio.run(
            generate_curriculum(session, series_id=series_id, provider=get_llm_provider())
        )
        session.commit()
        return len(episodes)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
