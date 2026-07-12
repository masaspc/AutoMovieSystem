"""コンテンツ検査(仕様§12)。決定的ルール(inspector.py流用)+LLM構造化レビュー。

予算超過(`BudgetExceededError`)はここでは捕捉しない。呼び出し元(service.py)の
`run_idempotent_async` がジョブを failed として記録し、例外をそのまま伝播する
(レビュー不合格ではなく「保留」として扱うため)。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.script import Script
from app.models.video_project import VideoProject
from app.providers.llm.base import LLMProvider
from app.schemas.content_review import ContentReviewFinding, ContentReviewResult
from app.services.llm_gateway import call_llm
from app.services.media.dialogue import extract_speech_lines
from app.services.reviews.findings import SEVERITY_BLOCKING, SEVERITY_WARNING, Finding
from app.services.scripts.inspector import inspect_script_with_history

PROMPT_VERSION = "content_review_v2"
OPERATION = "review_content"
# architecture.md モデルルーティングポリシー: 公開前最終判定 = high。
MODEL_POLICY = "high"

_BLOCKING_TECHNICAL_CODES = {
    "FACTUAL_ERROR",
    "MISLEADING_TECHNICAL_CLAIM",
    "TECHNICAL_ERROR",
}


def _normalize_llm_finding(item: ContentReviewFinding) -> Finding:
    """初心者教材の技術的誤りは、LLMのseverityが甘くても公開を止める。"""
    code = item.code
    severity = item.severity
    if code.upper() in _BLOCKING_TECHNICAL_CODES:
        severity = SEVERITY_BLOCKING
    return Finding(
        code,
        severity,
        item.message,
        item.detail,
    )


def build_idempotency_key(video_project_id: str, checksum: str) -> str:
    return f"review_content:{video_project_id}:{checksum}"


def _title_consistency_findings(script: Script) -> list[Finding]:
    """Script.title が台本の title_candidates に含まれているかを検査する。"""
    title_candidates = (script.body or {}).get("title_candidates") or []
    if title_candidates and script.title not in title_candidates:
        return [
            Finding(
                "title_not_in_candidates",
                SEVERITY_WARNING,
                f"Script.title「{script.title}」がtitle_candidatesに含まれていません",
            )
        ]
    return []


def _build_prompts(script: Script) -> tuple[str, str]:
    system_prompt = (
        "あなたはYouTube動画公開前の最終コンテンツレビュアーです。"
        "タイトル誠実性・誤認表現・スパム性・AI生成物である旨の開示要否・"
        "子ども向けコンテンツ該当性・著作権懸念の観点で台本を検査し、"
        "コードの実行結果やプログラミング言語の仕様も検証してください。技術的に誤った説明は"
        "初心者の学習を損なうためseverityをblockingにしてください。"
        "問題があれば findings に構造化して報告してください。"
        "問題がなければ findings は空配列、passed は true にしてください。"
    )
    body = script.body or {}
    sections_text = "\n".join(
        f"- {line.speaker}: {line.text}" for line in extract_speech_lines(body)
    )
    user_prompt = (
        f"動画タイトル: {script.title}\n"
        f"説明文: {body.get('description', '')}\n"
        f"hook: {script.hook or ''}\n"
        f"conclusion: {script.conclusion or ''}\n"
        f"call_to_action: {script.call_to_action or ''}\n"
        f"セクション:\n{sections_text}"
    )
    return system_prompt, user_prompt


async def inspect_content(
    session: Session,
    project: VideoProject,
    script: Script,
    *,
    provider: LLMProvider,
    job_run_id: str | None,
) -> list[Finding]:
    """決定的ルール検査+LLM構造化レビューを行い、Finding一覧を返す(仕様§12)。"""
    findings: list[Finding] = [
        Finding(f.code, f.severity, f.message) for f in inspect_script_with_history(session, script)
    ]
    findings.extend(_title_consistency_findings(script))

    system_prompt, user_prompt = _build_prompts(script)
    checksum = project.checksum or project.id
    idempotency_key = build_idempotency_key(project.id, checksum)

    result = await call_llm(
        session,
        provider,
        operation=OPERATION,
        prompt_version=PROMPT_VERSION,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_schema=ContentReviewResult,
        model_policy=MODEL_POLICY,
        idempotency_key=idempotency_key,
        job_run_id=job_run_id,
    )
    content_result = ContentReviewResult.model_validate(result.data)
    findings.extend(_normalize_llm_finding(item) for item in content_result.findings)

    return findings
