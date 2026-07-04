"""台本生成(仕様§10)。Topic+Evidenceからプロンプトを構築しLLMで台本を生成する。"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.evidence import Evidence
from app.models.job_run import JobRun
from app.models.script import Script
from app.models.topic import Topic
from app.providers.llm.base import LLMProvider
from app.schemas.script_content import ScriptContent
from app.services.jobs import run_idempotent_async
from app.services.llm_gateway import call_llm

logger = get_logger(__name__)

PROMPT_VERSION = "script_v1"
OPERATION = "generate_script"
# architecture.md モデルルーティングポリシー: 台本初稿 = mid。
MODEL_POLICY = "mid"


class TopicNotFoundError(ValueError):
    """指定されたTopicが存在しない場合。"""


def build_idempotency_key(topic_id: str, prompt_version: str = PROMPT_VERSION) -> str:
    return f"generate_script:{topic_id}:{prompt_version}"


def _build_prompts(topic: Topic, evidence_list: list[Evidence]) -> tuple[str, str]:
    system_prompt = (
        "あなたはYouTube動画の台本作家です。与えられた企画とリサーチ根拠(Evidence)をもとに、"
        "視聴者に価値を提供する構造化された台本を日本語で作成してください。"
        "narrationで数値(%・円・倍などの主張)を述べる場合は必ず対応するevidence_idsを付与してください。"
        "「絶対に儲かる」「必ず成功する」等の誇張・断定表現は使用しないでください。"
    )
    evidence_lines = "\n".join(
        f"- id={e.id} claim={e.claim} source={e.source_url}" for e in evidence_list
    )
    user_prompt = (
        f"企画タイトル: {topic.title}\n"
        f"企画概要: {topic.description or '(なし)'}\n"
        f"リサーチ根拠一覧:\n{evidence_lines or '(なし)'}\n"
    )
    return system_prompt, user_prompt


async def generate_script(
    session: Session,
    *,
    topic_id: str,
    provider: LLMProvider,
    trace_id: str | None = None,
) -> Script:
    """Topic+EvidenceからLLMで台本を生成し、Scriptレコードを作成する(冪等)。

    `JobRun(idempotency_key="generate_script:{topic_id}:{prompt_version}")` により、
    同一Topic・同一プロンプトバージョンでの再実行はLLMを再度呼ばず既存Scriptを返す。
    """
    topic = session.get(Topic, topic_id)
    if topic is None:
        raise TopicNotFoundError(f"Topic not found: {topic_id}")

    evidence_list = session.query(Evidence).filter(Evidence.topic_id == topic_id).all()
    system_prompt, user_prompt = _build_prompts(topic, evidence_list)
    idempotency_key = build_idempotency_key(topic_id)

    async def _do_generate(job_run: JobRun) -> Script:
        result = await call_llm(
            session,
            provider,
            operation=OPERATION,
            prompt_version=PROMPT_VERSION,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=ScriptContent,
            model_policy=MODEL_POLICY,
            idempotency_key=idempotency_key,
            job_run_id=job_run.id,
        )
        content = ScriptContent.model_validate(result.data)
        source_manifest = {"evidence_ids": [e.id for e in evidence_list]}

        max_version = (
            session.query(func.max(Script.version)).filter(Script.topic_id == topic_id).scalar()
        )
        version = 1 if max_version is None else max_version + 1

        title = content.title_candidates[0] if content.title_candidates else topic.title
        script = Script(
            topic_id=topic_id,
            version=version,
            title=title,
            hook=content.hook,
            body=content.model_dump(),
            conclusion=content.conclusion,
            call_to_action=content.call_to_action,
            source_manifest=source_manifest,
            model_name=result.model,
            prompt_version=PROMPT_VERSION,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            estimated_cost_micro_usd=result.estimated_cost_micro_usd,
            status="draft",
        )
        session.add(script)
        session.flush()
        return script

    job_result = await run_idempotent_async(
        session,
        job_type="generate_script",
        entity_type="topic",
        entity_id=topic_id,
        idempotency_key=idempotency_key,
        fn=_do_generate,
        trace_id=trace_id,
    )

    if job_result.status == "skipped":
        existing_script = (
            session.query(Script)
            .filter(Script.topic_id == topic_id, Script.prompt_version == PROMPT_VERSION)
            .order_by(Script.version.desc())
            .first()
        )
        if existing_script is None:  # pragma: no cover - 理論上到達しない防御的分岐
            raise RuntimeError(f"JobRun succeeded but Script not found for topic_id={topic_id}")
        return existing_script

    assert job_result.result is not None
    return job_result.result
