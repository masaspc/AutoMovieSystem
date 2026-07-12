"""シリーズカリキュラム生成Celeryタスク。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from celery import Task

from app.db.session import SessionLocal
from app.providers.llm.factory import get_llm_provider
from app.services.series.service import generate_curriculum
from app.workers.celery_app import celery_app


@celery_app.task(bind=True, name="series.generate_curriculum")
def generate_curriculum_task(self: Task, series_id: str) -> int:
    session = SessionLocal()
    started_at = datetime.now(UTC).isoformat()

    def report_progress(stage: str, current: int, total: int) -> None:
        self.update_state(
            state="PROGRESS",
            meta={
                "stage": stage,
                "current": current,
                "total": total,
                "started_at": started_at,
            },
        )

    try:
        episodes = asyncio.run(
            generate_curriculum(
                session,
                series_id=series_id,
                provider=get_llm_provider(),
                progress_callback=report_progress,
            )
        )
        session.commit()
        return len(episodes)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
