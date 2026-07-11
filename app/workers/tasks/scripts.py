"""台本生成Celeryタスク(薄いラッパー。ロジックは app.services.scripts.generator)。"""

from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.asset import Asset
from app.models.script import Script
from app.models.video_project import VideoProject
from app.providers.llm.factory import get_llm_provider
from app.schemas.production_settings import ProductionSettings
from app.services.jobs import record_failure_in_new_session
from app.services.media.dialogue import dialogue_script_enabled
from app.services.orchestration import link_script_and_advance
from app.services.scripts.generator import generate_script
from app.services.scripts.regenerator import regenerate_section
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="scripts.generate_script")
def generate_script_task(topic_id: str) -> str:
    """Topic IDから台本を生成する(冪等。D-013: asyncio.runでプロバイダーを呼ぶ)。

    生成後、同一Topicに紐づく既存のVideoProjectがあれば台本を紐付け、検査結果に
    応じて状態を前進させる(Web UIの「動画プロジェクト作成」が先に押されていた場合)。
    最新世代のVideoProjectに production_settings があれば台本生成へ反映する。
    """
    session = SessionLocal()
    try:
        projects = (
            session.query(VideoProject)
            .filter(VideoProject.topic_id == topic_id)
            .order_by(VideoProject.generation.desc())
            .all()
        )
        latest_project = projects[0] if projects else None
        production_settings = (
            ProductionSettings.model_validate(latest_project.production_settings)
            if latest_project is not None and latest_project.production_settings
            else None
        )

        provider = get_llm_provider()
        script = asyncio.run(
            generate_script(
                session,
                topic_id=topic_id,
                provider=provider,
                production_settings=production_settings,
            )
        )
        for project in projects:
            link_script_and_advance(session, project, script)
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


@celery_app.task(name="scripts.regenerate_section")
def regenerate_section_task(
    video_project_id: str, section_index: int, instruction: str = ""
) -> str:
    """動画プロジェクトの指定セクションだけを再生成して新Script versionへ差し替える。"""
    session = SessionLocal()
    try:
        project = session.get(VideoProject, video_project_id)
        if project is None or project.script_id is None:
            raise ValueError("VideoProjectまたはScriptが見つかりません")
        has_assets = (
            session.query(Asset).filter(Asset.video_project_id == video_project_id).first()
            is not None
        )
        editable_status = project.status in {
            "RESEARCH_READY",
            "SCRIPT_GENERATED",
            "SCRIPT_REVIEWED",
        }
        if not editable_status or has_assets:
            raise ValueError("素材生成後の台本は部分再生成できません")
        script = session.get(Script, project.script_id)
        if script is None:
            raise ValueError("Scriptが見つかりません")
        production_settings = ProductionSettings.model_validate(
            project.production_settings or ProductionSettings().model_dump()
        )
        edited = asyncio.run(
            regenerate_section(
                session,
                script=script,
                section_index=section_index,
                instruction=instruction,
                production_settings=production_settings,
                provider=get_llm_provider(),
                dialogue_enabled=dialogue_script_enabled(),
            )
        )
        project.script_id = edited.id
        session.commit()
        return edited.id
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
