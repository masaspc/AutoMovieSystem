from __future__ import annotations

import asyncio

from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.providers.llm.fake import DeterministicFakeLLMProvider
from app.schemas.content_review import ContentReviewFinding
from app.services.reviews.content import _normalize_llm_finding, inspect_content


def _make_project_and_script(db_session: Session, *, narration: str) -> tuple[VideoProject, Script]:
    channel = Channel(name="ch")
    db_session.add(channel)
    db_session.flush()

    topic = Topic(channel_id=channel.id, title="topic", source_type="manual", source_ref="r-1")
    db_session.add(topic)
    db_session.flush()

    script = Script(
        topic_id=topic.id,
        version=1,
        title="クリーンなタイトル",
        hook="hook",
        body={
            "title_candidates": ["クリーンなタイトル"],
            "description": "説明文",
            "promised_outcome": "視聴後に次の一歩が分かる",
            "sections": [
                {"heading": "本編", "narration": narration, "evidence_ids": []},
            ],
        },
        conclusion="まとめ",
        call_to_action="登録してください",
        source_manifest={"evidence_ids": []},
        status="reviewed",
    )
    db_session.add(script)
    db_session.flush()

    project = VideoProject(
        topic_id=topic.id,
        script_id=script.id,
        status="VIDEO_RENDERED",
        generation=1,
        target_duration_seconds=10,
        output_path="videos/test-project/out.mp4",
        checksum="a" * 64,
    )
    db_session.add(project)
    db_session.flush()
    return project, script


def test_clean_script_passes_with_no_blocking_findings(db_session: Session) -> None:
    project, script = _make_project_and_script(db_session, narration="今日はテーマを解説します。")
    provider = DeterministicFakeLLMProvider()

    findings = asyncio.run(
        inspect_content(db_session, project, script, provider=provider, job_run_id=None)
    )

    assert not [f for f in findings if f.severity == "blocking"]


def test_ng_word_in_narration_produces_blocking_finding_from_rule_inspector(
    db_session: Session,
) -> None:
    project, script = _make_project_and_script(db_session, narration="これは絶対に儲かる話です。")
    provider = DeterministicFakeLLMProvider()

    findings = asyncio.run(
        inspect_content(db_session, project, script, provider=provider, job_run_id=None)
    )

    assert any(f.code == "prohibited_expression" and f.severity == "blocking" for f in findings)


def test_ng_word_also_flagged_by_fake_llm_generator(db_session: Session) -> None:
    project, script = _make_project_and_script(db_session, narration="これは絶対に儲かる話です。")
    provider = DeterministicFakeLLMProvider()

    findings = asyncio.run(
        inspect_content(db_session, project, script, provider=provider, job_run_id=None)
    )

    assert any(f.code == "llm_flagged_exaggeration" and f.severity == "blocking" for f in findings)


def test_title_not_in_candidates_is_warning(db_session: Session) -> None:
    project, script = _make_project_and_script(db_session, narration="解説します。")
    script.title = "候補にないタイトル"
    db_session.flush()
    provider = DeterministicFakeLLMProvider()

    findings = asyncio.run(
        inspect_content(db_session, project, script, provider=provider, job_run_id=None)
    )

    assert any(f.code == "title_not_in_candidates" and f.severity == "warning" for f in findings)


def test_misleading_technical_claim_is_always_blocking() -> None:
    finding = _normalize_llm_finding(
        ContentReviewFinding(
            code="MISLEADING_TECHNICAL_CLAIM",
            severity="medium",
            message="Pythonの実行結果が誤っています",
        )
    )

    assert finding.severity == "blocking"
