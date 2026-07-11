"""台本生成(仕様§10)。Topic+Evidenceからプロンプトを構築しLLMで台本を生成する。"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.evidence import Evidence
from app.models.job_run import JobRun
from app.models.script import Script
from app.models.topic import Topic
from app.providers.llm.base import LLMProvider
from app.schemas.production_settings import ProductionSettings
from app.schemas.script_content import ScriptContent
from app.services.jobs import JobInProgressError, run_idempotent_async
from app.services.llm_gateway import call_llm
from app.services.scripts.duration import duration_within_range, estimate_duration_seconds

logger = get_logger(__name__)

PROMPT_VERSION = "script_v3"
OPERATION = "generate_script"
REPAIR_PROMPT_VERSION = "script_repair_v1"
REPAIR_OPERATION = "repair_script_duration"
# 尺が範囲外の場合の修復リトライ上限(仕様§10 D-013系: 無限リトライを避ける)。
MAX_REPAIR_ATTEMPTS = 2
# architecture.md モデルルーティングポリシー: 台本初稿・修復ともに mid。
MODEL_POLICY = "mid"

# script_template別の構成指示。
_SCRIPT_TEMPLATE_INSTRUCTIONS: dict[str, str] = {
    "explainer": "概念を順序立てて解説する構成にしてください。",
    "ranking": "ランキング形式で下位(N位)から1位へ向かって紹介する構成にしてください。",
    "problem_solution": "課題提示 → 原因分析 → 解決策提示、という構成にしてください。",
    "comparison": "複数の選択肢を共通の軸で比較し、最後に推奨を示す構成にしてください。",
    "story": "体験談・事例を用いたストーリー展開の構成にしてください。",
    "dialogue": "全編を掛け合い(キャラクター同士の会話)中心の構成にしてください。",
    "shorts": "冒頭の1文で結論を提示し、テンポよく展開する短尺構成にしてください。",
}

# 時間配分の目安(フック/本編/まとめ/CTA)。
_HOOK_RATIO = 0.10
_BODY_RATIO = 0.70
_CONCLUSION_RATIO = 0.12
_CTA_RATIO = 0.08


class TopicNotFoundError(ValueError):
    """指定されたTopicが存在しない場合。"""


def build_idempotency_key(
    topic_id: str, prompt_version: str = PROMPT_VERSION, settings_checksum: str = ""
) -> str:
    """`generate_script` の冪等キー。production_settingsのchecksumを含み、設定変更時は
    新しいキー(＝新しいJobRun)になり再生成される。
    """
    return f"generate_script:{topic_id}:{prompt_version}:{settings_checksum[:16]}"


def _duration_instruction(
    production_settings: ProductionSettings, *, dialogue_enabled: bool
) -> str:
    target_seconds = production_settings.target_duration_seconds
    char_target = production_settings.resolved_target_character_count()
    char_min = round(char_target * 0.9)
    char_max = round(char_target * 1.1)
    section_min = production_settings.min_sections
    section_max = production_settings.max_sections
    avg_sections = max(1, round((section_min + section_max) / 2))
    seconds_per_section = round(target_seconds / avg_sections)

    audio_text_instruction = (
        "この文字数はsections内のdialogue.textだけで満たしてください。"
        if dialogue_enabled
        else "この文字数はsections内のnarrationだけで満たしてください。"
    )
    return (
        f"目標尺は約{target_seconds}秒です。"
        f"台本の音声化対象文字数(ナレーション相当の合計)は{char_min}〜{char_max}文字"
        f"(目安{char_target}文字)に収めてください。"
        f"{audio_text_instruction}"
        "hook/conclusion/call_to_actionの文字数は含めず、必要な内容はsectionsにも反映してください。"
        f"セクション数は{section_min}〜{section_max}個にしてください。"
        f"1セクションあたりの目安は約{seconds_per_section}秒です。"
        f"時間配分の目安はフック約{round(_HOOK_RATIO * 100)}%・本編約{round(_BODY_RATIO * 100)}%・"
        f"まとめ約{round(_CONCLUSION_RATIO * 100)}%・CTA約{round(_CTA_RATIO * 100)}%です。"
        "冗長な繰り返し表現は避けてください。"
    )


def _template_instruction(production_settings: ProductionSettings) -> str:
    instruction = _SCRIPT_TEMPLATE_INSTRUCTIONS.get(production_settings.script_template, "")
    return f"台本の構成は次の方針に従ってください: {instruction}"


def _build_prompts(
    topic: Topic,
    evidence_list: list[Evidence],
    production_settings: ProductionSettings | None = None,
) -> tuple[str, str]:
    settings = get_settings()
    production_settings = production_settings or ProductionSettings()

    dialogue_instruction = ""
    if settings.DIALOGUE_SCRIPT_ENABLED:
        allowed_cast = _allowed_dialogue_cast()
        tsumugi_instruction = (
            "tsumugiは補足が有効な場面だけ登場させます。"
            if "tsumugi" in allowed_cast
            else "tsumugiはこの動画では使用しません。"
        )
        dialogue_instruction = (
            "各sectionのdialogueには、zundamonとmetanの掛け合いを2〜5行作成してください。"
            f"使用可能なspeakerは {', '.join(allowed_cast)} です。{tsumugi_instruction}"
            "emotionはneutral/happy/serious/surprisedのみを使ってください。"
            "話者の役割分担は zundamon=導入・素朴な疑問を投げかける役、"
            "metan=主説明・結論を述べる役、tsumugi=補足を加える役、としてください。"
            f"セリフ全体に占める掛け合いの比率目安は"
            f"{round(production_settings.dialogue_ratio * 100)}%です。"
        )

    duration_instruction = _duration_instruction(
        production_settings, dialogue_enabled=settings.DIALOGUE_SCRIPT_ENABLED
    )
    system_prompt = (
        "あなたはYouTube動画の台本作家です。与えられた企画とリサーチ根拠(Evidence)をもとに、"
        "視聴者に価値を提供する構造化された台本を日本語で作成してください。"
        "narrationで数値(%・円・倍などの主張)を述べる場合は必ず対応するevidence_idsを付与してください。"
        "「絶対に儲かる」「必ず成功する」等の誇張・断定表現は使用しないでください。"
        f"{duration_instruction}"
        f"{_template_instruction(production_settings)}"
        f"トーンは「{production_settings.tone}」を維持してください。"
        f"{dialogue_instruction}"
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


def _allowed_dialogue_cast() -> list[str]:
    configured_cast = {
        name.strip() for name in get_settings().DIALOGUE_CAST.split(",") if name.strip()
    }
    allowed_cast = [name for name in ("zundamon", "metan", "tsumugi") if name in configured_cast]
    if not {"zundamon", "metan"}.issubset(allowed_cast):
        return ["zundamon", "metan"]
    return allowed_cast


def _collect_evidence_ids(content: ScriptContent) -> set[str]:
    ids: set[str] = set()
    for section in content.sections:
        ids.update(section.evidence_ids)
    return ids


def _build_repair_prompt(
    content: ScriptContent, production_settings: ProductionSettings, estimated_seconds: float
) -> str:
    target_seconds = production_settings.target_duration_seconds
    if estimated_seconds < target_seconds:
        direction = "短い"
        adjustment = "具体例・比較・注意点・手順を追加して情報量を増やしてください。"
    else:
        direction = "長い"
        adjustment = "重複表現・冗長な言い回しを削除して簡潔にしてください。"

    return (
        f"現在の台本の推定尺は約{estimated_seconds:.0f}秒で、"
        f"目標尺{target_seconds}秒より{direction}です。{adjustment}"
        "evidence_idsを削除しないでください。新しい数値主張を根拠なく追加しないでください。"
        "台本全体の構造(title_candidates/hook/sections/conclusion/call_to_action等)を"
        "保ったまま、スキーマに適合するJSON全体を出力してください。\n\n"
        f"[修復対象の台本(JSON)]\n{content.model_dump_json()}"
    )


def _filter_dialogue_cast(content: ScriptContent, *, dialogue_enabled: bool) -> None:
    allowed_cast = set(_allowed_dialogue_cast()) if dialogue_enabled else set()
    for section in content.sections:
        section.dialogue = (
            [line for line in section.dialogue if line.speaker in allowed_cast]
            if dialogue_enabled
            else []
        )


async def generate_script(
    session: Session,
    *,
    topic_id: str,
    provider: LLMProvider,
    trace_id: str | None = None,
    production_settings: ProductionSettings | None = None,
) -> Script:
    """Topic+EvidenceからLLMで台本を生成し、Scriptレコードを作成する(冪等)。

    `JobRun(idempotency_key="generate_script:{topic_id}:{prompt_version}:{settings_checksum}")`
    により、同一Topic・同一プロンプトバージョン・同一production_settingsでの再実行は
    LLMを再度呼ばず既存Scriptを返す。`production_settings` が変わると再生成される。
    """
    topic = session.get(Topic, topic_id)
    if topic is None:
        raise TopicNotFoundError(f"Topic not found: {topic_id}")

    production_settings = production_settings or ProductionSettings()
    settings_checksum = production_settings.checksum()

    evidence_list = session.query(Evidence).filter(Evidence.topic_id == topic_id).all()
    system_prompt, user_prompt = _build_prompts(topic, evidence_list, production_settings)
    idempotency_key = build_idempotency_key(topic_id, PROMPT_VERSION, settings_checksum)

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
        total_input_tokens = result.input_tokens
        total_output_tokens = result.output_tokens
        total_estimated_cost_micro_usd = result.estimated_cost_micro_usd
        dialogue_enabled = get_settings().DIALOGUE_SCRIPT_ENABLED
        _filter_dialogue_cast(content, dialogue_enabled=dialogue_enabled)

        estimated_seconds = estimate_duration_seconds(
            content, production_settings, dialogue_enabled=dialogue_enabled
        )

        attempt = 0
        while (
            not duration_within_range(estimated_seconds, production_settings)
            and attempt < MAX_REPAIR_ATTEMPTS
        ):
            attempt += 1
            original_evidence_ids = _collect_evidence_ids(content)
            repair_user_prompt = _build_repair_prompt(
                content, production_settings, estimated_seconds
            )

            repair_result = await call_llm(
                session,
                provider,
                operation=REPAIR_OPERATION,
                prompt_version=REPAIR_PROMPT_VERSION,
                system_prompt=system_prompt,
                user_prompt=repair_user_prompt,
                response_schema=ScriptContent,
                model_policy=MODEL_POLICY,
                idempotency_key=f"{idempotency_key}:repair:{attempt}",
                job_run_id=job_run.id,
            )
            total_input_tokens += repair_result.input_tokens
            total_output_tokens += repair_result.output_tokens
            total_estimated_cost_micro_usd += repair_result.estimated_cost_micro_usd
            repaired_content = ScriptContent.model_validate(repair_result.data)
            _filter_dialogue_cast(repaired_content, dialogue_enabled=dialogue_enabled)
            repaired_evidence_ids = _collect_evidence_ids(repaired_content)

            if original_evidence_ids != repaired_evidence_ids:
                logger.warning(
                    "script_repair_discarded_evidence_ids_lost",
                    topic_id=topic_id,
                    attempt=attempt,
                    original_evidence_ids=sorted(original_evidence_ids),
                    repaired_evidence_ids=sorted(repaired_evidence_ids),
                )
                break

            content = repaired_content
            result = repair_result
            estimated_seconds = estimate_duration_seconds(
                content, production_settings, dialogue_enabled=dialogue_enabled
            )

        if not duration_within_range(estimated_seconds, production_settings):
            logger.warning(
                "script_duration_out_of_range_best_effort",
                topic_id=topic_id,
                estimated_seconds=estimated_seconds,
                min_duration_seconds=production_settings.min_duration_seconds,
                max_duration_seconds=production_settings.max_duration_seconds,
                attempts=attempt,
            )

        source_manifest = {
            "evidence_ids": [e.id for e in evidence_list],
            "estimated_duration_seconds": estimated_seconds,
            "production_settings_checksum": settings_checksum,
            "production_settings": production_settings.model_dump(),
        }

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
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            estimated_cost_micro_usd=total_estimated_cost_micro_usd,
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

    if job_result.status == "in_progress":
        raise JobInProgressError(f"generate_script already in progress: {idempotency_key}")

    if job_result.status == "skipped":
        candidates = (
            session.query(Script)
            .filter(Script.topic_id == topic_id, Script.prompt_version == PROMPT_VERSION)
            .order_by(Script.version.desc())
            .all()
        )
        existing_script = next(
            (
                candidate
                for candidate in candidates
                if (candidate.source_manifest or {}).get("production_settings_checksum")
                == settings_checksum
            ),
            None,
        )
        if existing_script is None:  # pragma: no cover - 理論上到達しない防御的分岐
            raise RuntimeError(f"JobRun succeeded but Script not found for topic_id={topic_id}")
        return existing_script

    assert job_result.result is not None
    return job_result.result
