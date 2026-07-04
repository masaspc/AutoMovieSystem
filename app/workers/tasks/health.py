"""疎通確認用のサンプルタスク。"""

from __future__ import annotations

from app.workers.celery_app import celery_app


@celery_app.task(name="health.ping")
def ping() -> str:
    return "pong"
