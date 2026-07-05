"""台本生成Celeryタスク(薄いラッパー。ロジックは app.services.scripts.generator)。"""

from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.providers.llm.factory import get_llm_provider
from app.services.jobs import record_failure_in_new_session
from app.services.scripts.generator import generate_script
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="scripts.generate_script")
def generate_script_task(topic_id: str) -> str:
    """Topic IDから台本を生成する(冪等。D-013: asyncio.runでプロバイダーを呼ぶ)。"""
    session = SessionLocal()
    try:
        provider = get_llm_provider()
        script = asyncio.run(generate_script(session, topic_id=topic_id, provider=provider))
        session.commit()
        return script.id
    except Exception as exc:
        session.rollback()
        idempotency_key = getattr(exc, "idempotency_key", None)
        if idempotency_key is not None:
            record_failure_in_new_session(
                idempotency_key=idempotency_key,
                job_type="generate_script",
                entity_type="topic",
                entity_id=topic_id,
                error=exc,
            )
        raise
    finally:
        session.close()
