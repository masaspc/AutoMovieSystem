"""Celeryアプリ(D-004: CELERY_TASK_ALWAYS_EAGER で同期実行に切替可能)。"""

from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "auto_movie_system",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.workers.tasks.health",
        "app.workers.tasks.scripts",
        "app.workers.tasks.media",
        "app.workers.tasks.publishing",
        "app.workers.tasks.analytics",
    ],
)

celery_app.conf.update(
    task_acks_late=True,
    task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
    task_eager_propagates=settings.CELERY_TASK_ALWAYS_EAGER,
    timezone="UTC",
    enable_utc=True,
    worker_prefetch_multiplier=1,
)

# 定期ジョブ(仕様§14)。`CELERY_TASK_ALWAYS_EAGER=true`(デフォルト)の環境では
# beatスケジューラを別途起動しない限り実行されず、既存の同期実行フローに影響しない。
celery_app.conf.beat_schedule = {
    "sync-completed-feedback-hourly": {
        "task": "analytics.sync_completed_feedback",
        "schedule": 3600.0,
    },
    # 修正5: 公開期日到来のPublication確定処理(published_at設定+状態遷移)を独立して15分毎に実行。
    "finalize-due-publications-every-15-minutes": {
        "task": "analytics.finalize_due_publications",
        "schedule": 900.0,
    },
}
