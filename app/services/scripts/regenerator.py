"""指定セクションだけをLLMで再生成し、新しいScript versionへ保存する。"""

from __future__ import annotations

import hashlib
from copy import deepcopy

from sqlalchemy.orm import Session

from app.models.job_run import JobRun
from app.models.script import Script
from app.providers.llm.base import LLMProvider
from app.schemas.production_settings import ProductionSettings
from app.schemas.script_content import ScriptContent, ScriptSection
from app.services.jobs import JobInProgressError, run_idempotent_async
from app.services.llm_gateway import call_llm
from app.services.scripts.editor import save_edited_script

OPERATION = "regenerate_script_section"
PROMPT_VERSION = "section_regenerate_v1"


async def regenerate_section(
    session: Session,
    *,
    script: Script,
    section_index: int,
    instruction: str,
    production_settings: ProductionSettings,
    provider: LLMProvider,
    dialogue_enabled: bool,
) -> Script:
    content = ScriptContent.model_validate(script.body)
    if not 0 <= section_index < len(content.sections):
        raise ValueError("section not found")
    original = content.sections[section_index]
    digest = hashlib.sha256(
        f"{script.id}:{section_index}:{instruction}:{production_settings.checksum()}".encode()
    ).hexdigest()
    idempotency_key = f"regenerate_section:{digest}"

    async def _do_regenerate(job_run: JobRun) -> Script:
        previous_heading = (
            content.sections[section_index - 1].heading if section_index else "(なし)"
        )
        next_heading = (
            content.sections[section_index + 1].heading
            if section_index + 1 < len(content.sections)
            else "(なし)"
        )
        user_prompt = (
            "次のYouTube台本セクションだけを書き直してください。Evidence IDは増減させず、"
            "新しい数値主張を追加しないでください。前後の文脈と指定を反映してください。\n"
            f"追加指定: {instruction or 'より分かりやすく改善'}\n"
            f"前のセクション: {previous_heading}\n"
            f"次のセクション: {next_heading}\n"
            f"[対象セクションJSON]\n{original.model_dump_json()}"
        )
        result = await call_llm(
            session,
            provider,
            operation=OPERATION,
            prompt_version=PROMPT_VERSION,
            system_prompt="あなたは根拠を保持して台本の一部分だけを改善する編集者です。",
            user_prompt=user_prompt,
            response_schema=ScriptSection,
            model_policy="mid",
            idempotency_key=idempotency_key,
            job_run_id=job_run.id,
        )
        regenerated = ScriptSection.model_validate(result.data)
        if set(regenerated.evidence_ids) != set(original.evidence_ids):
            raise ValueError("部分再生成でEvidence IDが変更されたため結果を破棄しました")
        body = deepcopy(script.body)
        body["sections"][section_index] = regenerated.model_dump()
        edited = save_edited_script(
            session,
            source=script,
            body=body,
            production_settings=production_settings,
            edit_reason=f"section_regenerate:{section_index}",
            dialogue_enabled=dialogue_enabled,
        )
        edited.model_name = result.model
        edited.prompt_version = PROMPT_VERSION
        edited.input_tokens = result.input_tokens
        edited.output_tokens = result.output_tokens
        edited.estimated_cost_micro_usd = result.estimated_cost_micro_usd
        edited.source_manifest = {
            **(edited.source_manifest or {}),
            "section_regeneration_key": idempotency_key,
        }
        return edited

    job_result = await run_idempotent_async(
        session,
        job_type=OPERATION,
        entity_type="script",
        entity_id=script.id,
        idempotency_key=idempotency_key,
        fn=_do_regenerate,
    )
    if job_result.status == "in_progress":
        raise JobInProgressError(f"section regeneration in progress: {idempotency_key}")
    if job_result.status == "skipped":
        candidates = (
            session.query(Script)
            .filter(Script.topic_id == script.topic_id)
            .order_by(Script.version.desc())
            .all()
        )
        existing = next(
            (
                candidate
                for candidate in candidates
                if (candidate.source_manifest or {}).get("section_regeneration_key")
                == idempotency_key
            ),
            None,
        )
        if existing is None:
            raise RuntimeError("completed regeneration Script not found")
        return existing
    assert job_result.result is not None
    return job_result.result
