"""管理画面からの台本セクション編集を新しいScript versionとして保存する。"""

from __future__ import annotations

from copy import deepcopy

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.script import Script
from app.schemas.production_settings import ProductionSettings
from app.schemas.script_content import ScriptContent
from app.services.scripts.duration import estimate_duration_seconds


class ScriptEditError(ValueError):
    """台本編集内容または対象が不正。"""


def save_edited_script(
    session: Session,
    *,
    source: Script,
    body: dict,
    production_settings: ProductionSettings,
    edit_reason: str,
    dialogue_enabled: bool,
) -> Script:
    """編集済み本文を検証し、元Scriptを変更せず新versionとして保存する。"""
    content = ScriptContent.model_validate(body)
    if not content.sections:
        raise ScriptEditError("台本には最低1つのセクションが必要です")
    content.chapters = [section.heading for section in content.sections]
    estimated_seconds = estimate_duration_seconds(
        content, production_settings, dialogue_enabled=dialogue_enabled
    )
    manifest = deepcopy(source.source_manifest or {})
    manifest.update(
        {
            "edited_from_script_id": source.id,
            "edit_reason": edit_reason,
            "estimated_duration_seconds": estimated_seconds,
            "production_settings": production_settings.model_dump(),
            "production_settings_checksum": production_settings.checksum(),
        }
    )
    max_version = (
        session.query(func.max(Script.version)).filter(Script.topic_id == source.topic_id).scalar()
    )
    title = content.title_candidates[0] if content.title_candidates else source.title
    script = Script(
        topic_id=source.topic_id,
        version=(max_version or 0) + 1,
        title=title,
        hook=content.hook,
        body=content.model_dump(),
        conclusion=content.conclusion,
        call_to_action=content.call_to_action,
        source_manifest=manifest,
        model_name=source.model_name,
        prompt_version="manual_edit_v1",
        input_tokens=0,
        output_tokens=0,
        estimated_cost_micro_usd=0,
        status="reviewed",
    )
    session.add(script)
    session.flush()
    return script
