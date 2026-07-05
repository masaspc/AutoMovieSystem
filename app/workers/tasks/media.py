"""メディア生成Celeryタスク(薄いラッパー。ロジックは app.services.media.pipeline)。"""

from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.providers.tts.factory import get_tts_provider
from app.services.media.pipeline import prepare_assets, render_video, synthesize_audio
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="media.prepare_assets")
def prepare_assets_task(video_project_id: str) -> str:
    """背景画像を生成しAsset登録する(冪等)。"""
    session = SessionLocal()
    try:
        project = prepare_assets(session, video_project_id=video_project_id)
        session.commit()
        return project.id
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(name="media.synthesize_audio")
def synthesize_audio_task(video_project_id: str) -> str:
    """VideoProjectに紐づくScriptの各sectionをTTSで音声化する(冪等)。"""
    session = SessionLocal()
    try:
        provider = get_tts_provider()
        assets = asyncio.run(
            synthesize_audio(session, video_project_id=video_project_id, provider=provider)
        )
        session.commit()
        return f"{len(assets)} assets synthesized"
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(name="media.render_video")
def render_video_task(video_project_id: str) -> str:
    """字幕生成+FFmpegレンダリング+ffprobe検証を行う(冪等。失敗時RENDER_FAILED)。"""
    session = SessionLocal()
    try:
        project = render_video(session, video_project_id=video_project_id)
        session.commit()
        return project.id
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
