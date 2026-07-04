"""Celeryアプリ(D-004: CELERY_TASK_ALWAYS_EAGER で同期実行に切替可能)。"""

from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "auto_movie_system",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.tasks.health", "app.workers.tasks.scripts"],
)

celery_app.conf.update(
    task_acks_late=True,
    task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
    task_eager_propagates=settings.CELERY_TASK_ALWAYS_EAGER,
    timezone="UTC",
    enable_utc=True,
    worker_prefetch_multiplier=1,
)
