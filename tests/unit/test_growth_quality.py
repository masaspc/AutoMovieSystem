from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.evidence import Evidence
from app.models.job_run import JobRun
from app.models.script import Script
from app.models.topic import Topic
from app.models.usage_record import UsageRecord
from app.providers.llm.base import StructuredLLMResult
from app.providers.llm.fake import DeterministicFakeLLMProvider
from app.schemas.growth_quality import GrowthQualityReport
from app.services.growth.quality import optimize_growth_quality


def _body(*, evidence_ids: list[str], title_count: int = 3) -> dict:
    titles = [
        "結論から学ぶ実践ガイド",
        "初心者が避けたい3つの失敗",
        "今日から始める最短手順",
    ][:title_count]
    return {
        "title_candidates": titles,
        "target_audience": "初めて学ぶ人",
        "viewer_problem": "何から始めるか分からない",
        "promised_outcome": "具体的な次の一歩が分かる",
        "hook": "最後まで見ると失敗を避ける具体的な手順が分かります。",
        "sections": [
            {
                "heading": "本編",
                "narration": "根拠を確認しながら、具体的な判断基準を説明します。",
                "visual_instruction": "要点と出典を表示する。",
                "evidence_ids": evidence_ids,
            }
        ],
        "conclusion": "要点を振り返ります。",
        "call_to_action": "続きも確認してください。",
        "description": "実践的な解説です。",
        "tags": ["解説"],
        "chapters": ["本編"],
        "thumbnail_texts": ["今すぐ確認", "3つの盲点", "結論はこれ"],
    }


def _make_script(
    db_session: Session,
    *,
    title_count: int = 3,
    evidence_status: str | None = "verified",
) -> tuple[Script, Evidence | None]:
    channel = Channel(name="growth-channel")
    db_session.add(channel)
    db_session.flush()
    topic = Topic(
        channel_id=channel.id,
        title="成長テーマ",
        source_type="manual",
        source_ref="growth-quality-topic",
    )
    db_session.add(topic)
    db_session.flush()
    evidence = None
    evidence_ids: list[str] = []
    if evidence_status is not None:
        evidence = Evidence(
            topic_id=topic.id,
            source_url="https://example.com/source",
            source_title="一次資料",
            claim="検証可能な主張",
            excerpt_hash="e" * 64,
            verification_status=evidence_status,
        )
        db_session.add(evidence)
        db_session.flush()
        evidence_ids.append(evidence.id)
    body = _body(evidence_ids=evidence_ids, title_count=title_count)
    script = Script(
        topic_id=topic.id,
        version=1,
        title=body["title_candidates"][0],
        hook=body["hook"],
        body=body,
        conclusion=body["conclusion"],
        call_to_action=body["call_to_action"],
        source_manifest={"evidence_ids": evidence_ids},
    )
    db_session.add(script)
    db_session.flush()
    return script, evidence


def test_growth_quality_schema_bounds_scores_and_lists() -> None:
    with pytest.raises(ValidationError):
        GrowthQualityReport.model_validate(
            {
                "appeal_score": 101,
                "engagement_score": 80,
                "satisfaction_score": 80,
                "originality_score": 80,
                "trust_score": 80,
                "overall_score": 80,
                "recommended_title_index": 0,
                "recommended_thumbnail_index": 0,
                "strengths": [str(index) for index in range(6)],
            }
        )


def test_preflight_persists_report_selects_packaging_and_records_usage(
    db_session: Session,
) -> None:
    script, _ = _make_script(db_session)
    original_titles = list(script.body["title_candidates"])
    original_thumbnails = list(script.body["thumbnail_texts"])

    outcome = asyncio.run(
        optimize_growth_quality(
            db_session, script_id=script.id, provider=DeterministicFakeLLMProvider()
        )
    )

    assert outcome.quick_approval_ready is True
    assert outcome.rewrite_performed is False
    assert script.title == original_titles[1]
    assert script.body["title_candidates"][0] == original_titles[1]
    assert script.body["thumbnail_texts"][0] == original_thumbnails[2]
    assert outcome.report.recommended_title_index == 0
    assert outcome.report.recommended_thumbnail_index == 0
    assert script.source_manifest["growth_quality_report"]["overall_score"] == 86
    assert script.source_manifest["growth_quick_approval_ready"] is True
    assert script.source_manifest["growth_optimization_key"].startswith("growth_quality:")
    usage = db_session.query(UsageRecord).all()
    assert [record.operation for record in usage] == ["growth_quality_review"]


def test_low_score_rewrites_once_preserves_evidence_and_is_idempotent(
    db_session: Session,
) -> None:
    script, evidence = _make_script(db_session, title_count=2)
    assert evidence is not None

    first = asyncio.run(
        optimize_growth_quality(
            db_session, script_id=script.id, provider=DeterministicFakeLLMProvider()
        )
    )
    first_usage_count = db_session.query(UsageRecord).count()
    second = asyncio.run(
        optimize_growth_quality(
            db_session, script_id=script.id, provider=DeterministicFakeLLMProvider()
        )
    )

    assert first.rewrite_performed is True
    assert first.quick_approval_ready is True
    assert second == first
    assert script.body["sections"][0]["evidence_ids"] == [evidence.id]
    assert len(script.body["title_candidates"]) == 3
    assert len(script.body["thumbnail_texts"]) == 3
    assert first_usage_count == 3
    assert db_session.query(UsageRecord).count() == first_usage_count
    assert db_session.query(JobRun).filter(JobRun.job_type == "growth_quality").count() == 1


@pytest.mark.parametrize("evidence_status", [None, "pending"])
def test_missing_or_only_pending_evidence_requires_human_attention(
    db_session: Session, evidence_status: str | None
) -> None:
    script, _ = _make_script(db_session, evidence_status=evidence_status)

    outcome = asyncio.run(
        optimize_growth_quality(
            db_session, script_id=script.id, provider=DeterministicFakeLLMProvider()
        )
    )

    assert outcome.quick_approval_ready is False
    assert outcome.report.human_check_reasons
    expected = "根拠Evidenceがない" if evidence_status is None else "すべてpending"
    assert expected in outcome.report.human_check_reasons[0]


def test_very_high_similarity_to_recent_channel_script_is_blocking(
    db_session: Session,
) -> None:
    script, _ = _make_script(db_session)
    topic = db_session.get(Topic, script.topic_id)
    assert topic is not None
    recent_topic = Topic(
        channel_id=topic.channel_id,
        title="以前のテーマ",
        source_type="manual",
        source_ref="recent-similar-topic",
    )
    db_session.add(recent_topic)
    db_session.flush()
    db_session.add(
        Script(
            topic_id=recent_topic.id,
            version=1,
            title=script.body["title_candidates"][1],
            hook=script.body["hook"],
            body=_body(evidence_ids=[]),
            source_manifest={},
        )
    )
    db_session.flush()

    outcome = asyncio.run(
        optimize_growth_quality(
            db_session, script_id=script.id, provider=DeterministicFakeLLMProvider()
        )
    )

    issue_codes = {issue.code for issue in outcome.report.issues}
    assert "cross_video_similarity_too_high" in issue_codes
    assert outcome.quick_approval_ready is False


class _RecordingFakeProvider(DeterministicFakeLLMProvider):
    def __init__(self) -> None:
        self.review_payloads: list[dict] = []

    async def generate_structured(self, **kwargs: object) -> StructuredLLMResult:
        if kwargs["operation"] == "growth_quality_review":
            self.review_payloads.append(json.loads(str(kwargs["user_prompt"])))
        return await super().generate_structured(**kwargs)  # type: ignore[arg-type]


def test_review_context_is_limited_to_ten_recent_channel_scripts(db_session: Session) -> None:
    script, _ = _make_script(db_session)
    topic = db_session.get(Topic, script.topic_id)
    assert topic is not None
    for index in range(12):
        recent_topic = Topic(
            channel_id=topic.channel_id,
            title=f"過去テーマ{index}",
            source_type="manual",
            source_ref=f"recent-{index}",
        )
        db_session.add(recent_topic)
        db_session.flush()
        body = _body(evidence_ids=[])
        body["hook"] = f"過去のフック{index}"
        db_session.add(
            Script(
                topic_id=recent_topic.id,
                version=1,
                title=f"過去の動画{index}",
                hook=body["hook"],
                body=body,
                source_manifest={},
            )
        )
    db_session.flush()
    provider = _RecordingFakeProvider()

    asyncio.run(optimize_growth_quality(db_session, script_id=script.id, provider=provider))

    assert len(provider.review_payloads) == 1
    assert len(provider.review_payloads[0]["recent_channel_scripts"]) == 10


class _EvidenceChangingProvider(DeterministicFakeLLMProvider):
    async def generate_structured(self, **kwargs: object) -> StructuredLLMResult:
        result = await super().generate_structured(**kwargs)  # type: ignore[arg-type]
        if kwargs["operation"] != "growth_quality_rewrite":
            return result
        changed = dict(result.data)
        changed["sections"] = [dict(section) for section in result.data["sections"]]
        changed["sections"][0]["evidence_ids"] = []
        return StructuredLLMResult(
            data=changed,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            estimated_cost_micro_usd=result.estimated_cost_micro_usd,
            latency_ms=result.latency_ms,
        )


def test_rewrite_that_changes_aggregate_evidence_ids_is_discarded(db_session: Session) -> None:
    script, evidence = _make_script(db_session, title_count=2)
    assert evidence is not None
    original_body = script.body.copy()

    outcome = asyncio.run(
        optimize_growth_quality(
            db_session, script_id=script.id, provider=_EvidenceChangingProvider()
        )
    )

    assert outcome.rewrite_performed is False
    assert outcome.quick_approval_ready is False
    assert script.body["sections"][0]["narration"] == original_body["sections"][0]["narration"]
    assert script.body["sections"][0]["evidence_ids"] == [evidence.id]
    assert "rewrite_changed_evidence_ids" in {issue.code for issue in outcome.report.issues}
    assert (
        db_session.query(UsageRecord)
        .filter(UsageRecord.operation == "growth_quality_rewrite")
        .count()
        == 1
    )
