"""1クリック一括制作Celeryタスク(台本→素材→音声→レンダリング→自動レビュー)。

毎日投稿の運用でクリック数を最小化するため、制作工程を1タスクで完走させる。
承認・アップロードは含まない(人間承認のfail-closedゲートは変更しない)。
各工程は既存サービスの冪等キーで保護されており、失敗後の再実行は完了済み工程を
スキップして途中から再開する。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from celery import Task

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.providers.llm.factory import get_llm_provider
from app.providers.tts.factory import get_tts_provider
from app.providers.youtube.factory import get_youtube_provider
from app.services.orchestration import PipelineProviders, run_production_pipeline
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(bind=True, name="production.produce_video")
def produce_video_task(self: Task, topic_id: str) -> str:
    """Topicから自動レビューまでを一括実行する(冪等・進捗をPROGRESSで報告)。"""
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
        providers = PipelineProviders(
            llm=get_llm_provider(),
            tts=get_tts_provider(),
            youtube=get_youtube_provider(),
        )
        result = asyncio.run(
            run_production_pipeline(
                session,
                topic_id=topic_id,
                providers=providers,
                progress_callback=report_progress,
            )
        )
        session.commit()
        logger.info(
            "produce_video_completed",
            topic_id=topic_id,
            video_project_id=result.project.id,
            status=result.project.status,
            skipped_steps=result.skipped_steps,
        )
        return result.project.id
    except Exception:
        # 各工程内のJobRun失敗記録・失敗状態遷移は工程側サービス+コミット単位で
        # 永続化済み(orchestrationは工程ごとにcommitする)。ここでは未コミット分のみ
        # 巻き戻して失敗をタスク結果に伝える(画面はジョブ一覧から再実行できる)。
        session.rollback()
        raise
    finally:
        session.close()
