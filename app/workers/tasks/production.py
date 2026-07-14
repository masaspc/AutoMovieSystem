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


def select_auto_produce_topics(session, limit: int) -> list[str]:  # type: ignore[no-untyped-def]
    """夜間自動制作の対象Topicを選ぶ(スコア上位・未制作のもの)。

    対象: VideoProjectが存在しない、または台本段階(SCRIPT_REVIEWED以前)で止まっている
    Topic。制作中(ASSETS_READY以降)・失敗状態(人間の判断待ち)・完了済みは除外する。
    シリーズの「制作開始」済みエピソードのTopicも同じ条件で自然に対象になる。
    """
    from app.models.topic import Topic
    from app.models.video_project import VideoProject

    producible_states = (
        "TOPIC_CREATED",
        "TOPIC_SCORED",
        "RESEARCH_READY",
        "SCRIPT_GENERATED",
        "SCRIPT_REVIEWED",
    )
    selected: list[str] = []
    topics = session.query(Topic).order_by(Topic.total_score.desc(), Topic.created_at.asc()).all()
    for topic in topics:
        if len(selected) >= limit:
            break
        latest_project = (
            session.query(VideoProject)
            .filter(VideoProject.topic_id == topic.id)
            .order_by(VideoProject.generation.desc())
            .first()
        )
        if latest_project is None or latest_project.status in producible_states:
            selected.append(topic.id)
    return selected


@celery_app.task(name="production.auto_produce_daily")
def auto_produce_daily() -> int:
    """夜間自動制作(beat): 未制作の企画スコア上位N本を自動レビューまで一括制作する。

    朝には承認待ちの動画が並んでいる状態を作る(人間の作業は承認のみ)。
    N=AUTO_PRODUCE_DAILY_COUNT(0で無効)。各制作は既存の冪等パイプラインで、
    失敗しても翌晩の再実行で途中から再開される。
    """
    from app.core.config import get_settings

    limit = get_settings().AUTO_PRODUCE_DAILY_COUNT
    if limit <= 0:
        return 0

    session = SessionLocal()
    try:
        topic_ids = select_auto_produce_topics(session, limit)
    finally:
        session.close()

    for topic_id in topic_ids:
        produce_video_task.delay(topic_id)
        logger.info("auto_produce_dispatched", topic_id=topic_id)
    return len(topic_ids)
