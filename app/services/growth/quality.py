"""Pre-production growth-quality review and one-shot automatic improvement."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy.orm import Session

from app.models.evidence import Evidence
from app.models.job_run import JobRun
from app.models.script import Script
from app.models.topic import Topic
from app.providers.llm.base import LLMProvider
from app.schemas.growth_quality import (
    GrowthQualityIssue,
    GrowthQualityOutcome,
    GrowthQualityReport,
)
from app.schemas.script_content import ScriptContent
from app.services.jobs import JobInProgressError, run_idempotent_async
from app.services.llm_gateway import call_llm

REVIEW_OPERATION = "growth_quality_review"
REWRITE_OPERATION = "growth_quality_rewrite"
REVIEW_PROMPT_VERSION = "growth_quality_review_v1"
REWRITE_PROMPT_VERSION = "growth_quality_rewrite_v1"
MODEL_POLICY = "mid"
DEFAULT_SCORE_THRESHOLD = 75
MIN_COMPONENT_SCORE = 60
RECENT_SCRIPT_LIMIT = 10
VERY_HIGH_SIMILARITY = 0.9
DISTINCT_CANDIDATE_SIMILARITY = 0.82

_REVIEW_SYSTEM_PROMPT = (
    "あなたはYouTube動画の公開前グロース編集者です。台本を、クリックしたくなる魅力、"
    "冒頭から最後までのエンゲージメント、視聴後の満足度、チャンネル内での独自性、"
    "根拠に対する信頼性の6観点で0〜100点評価してください。誇張でクリックを稼がず、"
    "タイトル・サムネイル・フックの約束と本編の価値を一致させてください。"
    "強み、具体的な問題、実行可能な修正指示、人間確認が本当に必要な理由だけを、"
    "指定された件数上限内で構造化して返してください。"
)

_REWRITE_SYSTEM_PROMPT = (
    "あなたはYouTube動画のシニア構成作家です。品質レビューの修正指示を反映し、"
    "ScriptContent全体を1回だけ改善してください。冒頭で視聴価値を明示し、"
    "情報密度、展開、独自の解説、誠実なCTAを改善してください。"
    "title_candidatesとthumbnail_textsは、切り口が明確に異なる案を必ず3件ずつ作ってください。"
    "各sectionのevidence_idsは、重複数を含め全体として追加・削除・置換してはいけません。"
    "新しい事実や数値を根拠なく追加せず、スキーマに適合するJSONのみ返してください。"
)


class ScriptNotFoundError(ValueError):
    """Requested script does not exist."""


def build_growth_quality_idempotency_key(
    script: Script, *, score_threshold: int, allow_rewrite: bool
) -> str:
    """Use the immutable script version, not the body which this job intentionally changes."""
    return (
        f"growth_quality:{script.id}:v{script.version}:"
        f"threshold{score_threshold}:rewrite{int(allow_rewrite)}"
    )


def _normalize_similarity_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^\w]+", "", normalized, flags=re.UNICODE)


def _similarity(left: str, right: str) -> float:
    left_normalized = _normalize_similarity_text(left)
    right_normalized = _normalize_similarity_text(right)
    if not left_normalized or not right_normalized:
        return 0.0
    return SequenceMatcher(None, left_normalized, right_normalized, autojunk=False).ratio()


def _genuinely_distinct(values: list[str]) -> list[str]:
    """Greedily retain non-empty candidates that are not near-duplicates."""
    distinct: list[str] = []
    for raw_value in values:
        value = raw_value.strip()
        if not value:
            continue
        if all(
            _similarity(value, existing) < DISTINCT_CANDIDATE_SIMILARITY for existing in distinct
        ):
            distinct.append(value)
    return distinct


def _recent_channel_scripts(session: Session, script: Script, topic: Topic) -> list[Script]:
    return (
        session.query(Script)
        .join(Topic, Script.topic_id == Topic.id)
        .filter(Topic.channel_id == topic.channel_id, Script.id != script.id)
        .order_by(Script.created_at.desc(), Script.id.desc())
        .limit(RECENT_SCRIPT_LIMIT)
        .all()
    )


def _evidence_summary(session: Session, topic_id: str) -> tuple[list[Evidence], dict[str, int]]:
    evidence = session.query(Evidence).filter(Evidence.topic_id == topic_id).all()
    counts = Counter(item.verification_status for item in evidence)
    return evidence, dict(sorted(counts.items()))


def _build_review_prompt(
    *,
    script: Script,
    content: ScriptContent,
    recent_scripts: list[Script],
    evidence: list[Evidence],
    evidence_status_counts: dict[str, int],
) -> str:
    payload = {
        "script_title": script.title,
        "script": content.model_dump(mode="json"),
        "recent_channel_scripts": [
            {"title": recent.title, "hook": recent.hook or ""} for recent in recent_scripts
        ],
        "evidence": {
            "count": len(evidence),
            "status_counts": evidence_status_counts,
            "claims": [item.claim for item in evidence[:20]],
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _build_rewrite_prompt(content: ScriptContent, report: GrowthQualityReport) -> str:
    payload = {
        "script": content.model_dump(mode="json"),
        "quality_report": report.model_dump(mode="json"),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _aggregate_evidence_ids(content: ScriptContent) -> Counter[str]:
    return Counter(
        evidence_id for section in content.sections for evidence_id in section.evidence_ids
    )


def _candidate_at(values: list[str], index: int, fallback: str) -> str:
    if 0 <= index < len(values) and values[index].strip():
        return values[index].strip()
    return fallback


def _deterministic_findings(
    *,
    script: Script,
    content: ScriptContent,
    report: GrowthQualityReport,
    recent_scripts: list[Script],
    evidence: list[Evidence],
) -> tuple[list[GrowthQualityIssue], list[str]]:
    issues: list[GrowthQualityIssue] = []
    human_reasons: list[str] = []

    distinct_titles = _genuinely_distinct(content.title_candidates)
    if len(distinct_titles) < 3:
        issues.append(
            GrowthQualityIssue(
                code="title_candidates_not_distinct",
                severity="blocking",
                message="タイトル案は切り口が異なる3案を用意してください。",
            )
        )
    distinct_thumbnails = _genuinely_distinct(content.thumbnail_texts)
    if len(distinct_thumbnails) < 3:
        issues.append(
            GrowthQualityIssue(
                code="thumbnail_texts_not_distinct",
                severity="blocking",
                message="サムネイル文言は切り口が異なる3案を用意してください。",
            )
        )

    if report.recommended_title_index >= len(content.title_candidates):
        issues.append(
            GrowthQualityIssue(
                code="recommended_title_index_out_of_range",
                severity="blocking",
                message="推奨タイトル番号がタイトル候補の範囲外です。",
            )
        )
    if report.recommended_thumbnail_index >= len(content.thumbnail_texts):
        issues.append(
            GrowthQualityIssue(
                code="recommended_thumbnail_index_out_of_range",
                severity="blocking",
                message="推奨サムネイル番号が候補の範囲外です。",
            )
        )

    selected_title = _candidate_at(
        content.title_candidates, report.recommended_title_index, script.title
    )
    current_packaging = f"{selected_title}\n{content.hook}"
    similar_script = max(
        recent_scripts,
        key=lambda recent: _similarity(current_packaging, f"{recent.title}\n{recent.hook or ''}"),
        default=None,
    )
    if similar_script is not None:
        similarity = _similarity(
            current_packaging, f"{similar_script.title}\n{similar_script.hook or ''}"
        )
        if similarity >= VERY_HIGH_SIMILARITY:
            issues.append(
                GrowthQualityIssue(
                    code="cross_video_similarity_too_high",
                    severity="blocking",
                    message=(
                        "同じチャンネルの直近動画とタイトル・フックが酷似しています"
                        f"(類似度{similarity:.0%})。別の切り口へ変更してください。"
                    ),
                )
            )

    if not evidence:
        human_reasons.append("根拠Evidenceがないため、事実主張を人間が確認してください。")
    elif all(item.verification_status == "pending" for item in evidence):
        human_reasons.append(
            "Evidenceがすべてpendingのため、少なくとも主要な根拠を確認してください。"
        )
    return issues, human_reasons


def _merge_report(
    report: GrowthQualityReport,
    *,
    deterministic_issues: list[GrowthQualityIssue],
    deterministic_human_reasons: list[str],
) -> GrowthQualityReport:
    issues: list[GrowthQualityIssue] = []
    seen_codes: set[str] = set()
    for issue in [*deterministic_issues, *report.issues]:
        if issue.code in seen_codes:
            continue
        seen_codes.add(issue.code)
        issues.append(issue)
        if len(issues) == 8:
            break

    human_reasons: list[str] = []
    for reason in [*deterministic_human_reasons, *report.human_check_reasons]:
        normalized = reason.strip()
        if normalized and normalized not in human_reasons:
            human_reasons.append(normalized)
        if len(human_reasons) == 5:
            break
    return report.model_copy(update={"issues": issues, "human_check_reasons": human_reasons})


def _quick_approval_ready(report: GrowthQualityReport, *, score_threshold: int) -> bool:
    component_scores = (
        report.appeal_score,
        report.engagement_score,
        report.satisfaction_score,
        report.originality_score,
        report.trust_score,
    )
    return (
        report.overall_score >= score_threshold
        and min(component_scores) >= MIN_COMPONENT_SCORE
        and not any(issue.severity == "blocking" for issue in report.issues)
        and not report.human_check_reasons
    )


def _apply_recommendations(
    script: Script, content: ScriptContent, report: GrowthQualityReport
) -> GrowthQualityReport:
    title_index = report.recommended_title_index
    if 0 <= title_index < len(content.title_candidates):
        selected_title = content.title_candidates.pop(title_index).strip()[:255]
        content.title_candidates.insert(0, selected_title)
        script.title = selected_title
        title_index = 0

    thumbnail_index = report.recommended_thumbnail_index
    if 0 <= thumbnail_index < len(content.thumbnail_texts):
        selected_thumbnail = content.thumbnail_texts.pop(thumbnail_index).strip()
        content.thumbnail_texts.insert(0, selected_thumbnail)
        thumbnail_index = 0

    script.hook = content.hook
    script.conclusion = content.conclusion
    script.call_to_action = content.call_to_action
    script.body = content.model_dump(mode="json")
    return report.model_copy(
        update={
            "recommended_title_index": title_index,
            "recommended_thumbnail_index": thumbnail_index,
        }
    )


def _load_persisted_outcome(script: Script) -> GrowthQualityOutcome:
    manifest = script.source_manifest or {}
    report_data = manifest.get("growth_quality_report")
    if not isinstance(report_data, dict):
        raise RuntimeError(f"Growth quality JobRun succeeded but report is missing: {script.id}")
    return GrowthQualityOutcome(
        report=GrowthQualityReport.model_validate(report_data),
        quick_approval_ready=bool(manifest.get("growth_quick_approval_ready", False)),
        rewrite_performed=bool(manifest.get("growth_quality_rewrite_performed", False)),
    )


async def optimize_growth_quality(
    session: Session,
    *,
    script_id: str,
    provider: LLMProvider,
    score_threshold: int = DEFAULT_SCORE_THRESHOLD,
    allow_rewrite: bool = True,
    trace_id: str | None = None,
) -> GrowthQualityOutcome:
    """Review a script, optionally rewrite it once, and persist a quick-approval decision."""
    if not 0 <= score_threshold <= 100:
        raise ValueError("score_threshold must be between 0 and 100")
    script = session.get(Script, script_id)
    if script is None:
        raise ScriptNotFoundError(f"Script not found: {script_id}")
    topic = session.get(Topic, script.topic_id)
    if topic is None:  # pragma: no cover - protected by the foreign key
        raise ValueError(f"Topic not found: {script.topic_id}")

    idempotency_key = build_growth_quality_idempotency_key(
        script, score_threshold=score_threshold, allow_rewrite=allow_rewrite
    )

    async def _do_optimize(job_run: JobRun) -> GrowthQualityOutcome:
        content = ScriptContent.model_validate(script.body)
        recent_scripts = _recent_channel_scripts(session, script, topic)
        evidence, status_counts = _evidence_summary(session, topic.id)

        async def _review(round_number: int, current_content: ScriptContent) -> GrowthQualityReport:
            result = await call_llm(
                session,
                provider,
                operation=REVIEW_OPERATION,
                prompt_version=REVIEW_PROMPT_VERSION,
                system_prompt=_REVIEW_SYSTEM_PROMPT,
                user_prompt=_build_review_prompt(
                    script=script,
                    content=current_content,
                    recent_scripts=recent_scripts,
                    evidence=evidence,
                    evidence_status_counts=status_counts,
                ),
                response_schema=GrowthQualityReport,
                model_policy=MODEL_POLICY,
                idempotency_key=f"{idempotency_key}:review:{round_number}",
                job_run_id=job_run.id,
            )
            return GrowthQualityReport.model_validate(result.data)

        report = await _review(1, content)
        rewrite_performed = False
        rewrite_attempted = bool(
            (script.source_manifest or {}).get("growth_quality_rewrite_attempted", False)
        )

        if allow_rewrite and report.overall_score < score_threshold and not rewrite_attempted:
            rewrite_attempted = True
            original_evidence_ids = _aggregate_evidence_ids(content)
            rewrite_result = await call_llm(
                session,
                provider,
                operation=REWRITE_OPERATION,
                prompt_version=REWRITE_PROMPT_VERSION,
                system_prompt=_REWRITE_SYSTEM_PROMPT,
                user_prompt=_build_rewrite_prompt(content, report),
                response_schema=ScriptContent,
                model_policy=MODEL_POLICY,
                idempotency_key=f"{idempotency_key}:rewrite:1",
                job_run_id=job_run.id,
            )
            rewritten_content = ScriptContent.model_validate(rewrite_result.data)
            if _aggregate_evidence_ids(rewritten_content) == original_evidence_ids:
                content = rewritten_content
                rewrite_performed = True
                report = await _review(2, content)
            else:
                report = _merge_report(
                    report,
                    deterministic_issues=[
                        GrowthQualityIssue(
                            code="rewrite_changed_evidence_ids",
                            severity="blocking",
                            message=(
                                "自動書き直しがEvidence参照を変更したため、安全のため採用しませんでした。"
                            ),
                        )
                    ],
                    deterministic_human_reasons=[
                        "自動書き直しを破棄したため、元台本の修正指示を確認してください。"
                    ],
                )

        deterministic_issues, deterministic_human_reasons = _deterministic_findings(
            script=script,
            content=content,
            report=report,
            recent_scripts=recent_scripts,
            evidence=evidence,
        )
        report = _merge_report(
            report,
            deterministic_issues=deterministic_issues,
            deterministic_human_reasons=deterministic_human_reasons,
        )
        quick_approval_ready = _quick_approval_ready(report, score_threshold=score_threshold)
        report = _apply_recommendations(script, content, report)

        manifest: dict[str, Any] = {
            **(script.source_manifest or {}),
            "growth_quality_report": report.model_dump(mode="json"),
            "growth_quick_approval_ready": quick_approval_ready,
            "growth_optimization_key": idempotency_key,
            "growth_quality_rewrite_attempted": rewrite_attempted,
            "growth_quality_rewrite_performed": rewrite_performed,
            "growth_quality_prompt_version": REVIEW_PROMPT_VERSION,
        }
        script.source_manifest = manifest
        session.flush()
        return GrowthQualityOutcome(
            report=report,
            quick_approval_ready=quick_approval_ready,
            rewrite_performed=rewrite_performed,
        )

    job_result = await run_idempotent_async(
        session,
        job_type="growth_quality",
        entity_type="script",
        entity_id=script.id,
        idempotency_key=idempotency_key,
        fn=_do_optimize,
        trace_id=trace_id,
    )
    if job_result.status == "in_progress":
        raise JobInProgressError(
            f"growth quality optimization already in progress: {idempotency_key}"
        )
    if job_result.status == "skipped":
        return _load_persisted_outcome(script)
    assert job_result.result is not None
    return job_result.result


# A descriptive alias for callers that treat this as a review gate rather than an optimizer.
run_growth_quality_preflight = optimize_growth_quality
