from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.channel import Channel
from app.models.evidence import Evidence
from app.models.insight import Insight
from app.models.publication import Publication
from app.models.script import Script
from app.models.topic import Topic
from app.models.usage_record import UsageRecord
from app.models.video_metric_daily import VideoMetricDaily
from app.models.video_project import VideoProject
from app.providers.llm.base import StructuredLLMResult
from app.providers.llm.fake import DeterministicFakeLLMProvider
from app.schemas.production_settings import ProductionSettings
from app.services.scripts.generator import (
    PROMPT_VERSION,
    _build_prompts,
    _build_recent_performance_context,
    build_idempotency_key,
    generate_script,
)


def _make_topic(db_session: Session) -> Topic:
    channel = Channel(name="tech-channel")
    db_session.add(channel)
    db_session.flush()

    topic = Topic(
        channel_id=channel.id,
        title="生成AIの基礎",
        description="初心者向けの解説",
        source_type="manual",
        source_ref="client-key-1",
    )
    db_session.add(topic)
    db_session.flush()

    evidence = Evidence(
        topic_id=topic.id,
        source_url="https://example.com/a",
        source_title="出典A",
        claim="市場は年10%成長している",
        excerpt_hash="abc123",
    )
    db_session.add(evidence)
    db_session.flush()
    return topic


def test_generate_script_creates_script_with_evidence_manifest(db_session: Session) -> None:
    topic = _make_topic(db_session)
    provider = DeterministicFakeLLMProvider()

    script = asyncio.run(generate_script(db_session, topic_id=topic.id, provider=provider))
    db_session.commit()

    assert script.topic_id == topic.id
    assert script.version == 1
    assert script.status == "draft"
    assert script.title
    assert script.body["title_candidates"]
    evidence_ids = script.source_manifest["evidence_ids"]
    assert len(evidence_ids) == 1

    assert db_session.query(Script).filter(Script.topic_id == topic.id).count() == 1
    # script_v3(Phase 2尺検査): 既定のFake台本はshort下限を下回るため、上限2回の
    # 尺修復が発生する。UsageRecordは初回生成1件+修復2件。
    assert db_session.query(UsageRecord).count() == 3


def test_generate_script_is_idempotent(db_session: Session) -> None:
    topic = _make_topic(db_session)
    provider = DeterministicFakeLLMProvider()

    script1 = asyncio.run(generate_script(db_session, topic_id=topic.id, provider=provider))
    db_session.commit()
    script2 = asyncio.run(generate_script(db_session, topic_id=topic.id, provider=provider))
    db_session.commit()

    assert script1.id == script2.id
    assert db_session.query(Script).filter(Script.topic_id == topic.id).count() == 1
    # 1回目呼び出しでのみ生成+尺修復2回(理由は上のテストのコメント参照)が走り、
    # 2回目はJobRunがskipされるためUsageRecordは増えない。
    assert db_session.query(UsageRecord).count() == 3


def test_dialogue_prompt_is_explicitly_opt_in(db_session: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    topic = _make_topic(db_session)

    monkeypatch.setenv("DIALOGUE_SCRIPT_ENABLED", "false")
    get_settings.cache_clear()
    normal_prompt, _ = _build_prompts(topic, [])
    assert "dialogue" not in normal_prompt

    monkeypatch.setenv("DIALOGUE_SCRIPT_ENABLED", "true")
    get_settings.cache_clear()
    dialogue_prompt, _ = _build_prompts(topic, [])
    assert "zundamonとmetan" in dialogue_prompt


def test_prompt_includes_duration_and_template_instructions(db_session: Session) -> None:
    """system_promptに目標尺・目標文字数・セクション数範囲・時間配分・テンプレート指示が含まれる。"""
    topic = _make_topic(db_session)
    settings = ProductionSettings.from_preset("standard_3min").model_copy(
        update={"script_template": "ranking"}
    )

    system_prompt, _ = _build_prompts(topic, [], settings)

    assert "180秒" in system_prompt  # 目標尺
    char_target = settings.resolved_target_character_count()
    assert str(char_target) in system_prompt  # 目標文字数(目安)
    assert "3〜5個" in system_prompt  # セクション数範囲
    assert "フック約10%" in system_prompt
    assert "本編約70%" in system_prompt
    assert "まとめ約12%" in system_prompt
    assert "CTA約8%" in system_prompt
    assert "ランキング形式" in system_prompt  # script_template=ranking の指示文


def test_prompt_requires_honest_growth_composition_for_all_formats(
    db_session: Session,
) -> None:
    topic = _make_topic(db_session)

    short_prompt, _ = _build_prompts(topic, [], ProductionSettings.from_preset("short"))
    standard_prompt, _ = _build_prompts(topic, [], ProductionSettings.from_preset("standard_3min"))

    for prompt in (short_prompt, standard_prompt):
        assert "訴求軸が実質的に異なり" in prompt
        assert "title_candidates" in prompt and "ちょうど3案" in prompt
        assert "thumbnail_textsも必ずちょうど3案" in prompt
        assert "一対一で同じ約束" in prompt
        assert "冒頭30秒以内に必ず提示" in prompt
        assert "挨拶・自己紹介・チャンネル説明から始めず" in prompt
        assert "最も強い価値・結果・意外な根拠" in prompt
        assert "visual_instructionに具体的に記載" in prompt
        assert "この動画固有の切り口" in prompt
        assert "RSSの見出し・要約を順番に読み上げるのではなく" in prompt
        assert "登録後に継続して得られる具体的な価値" in prompt
        assert "次の動画またはシリーズ" in prompt

    assert "Shortsでは1秒目" in short_prompt
    assert "次のShortまたはシリーズ" in short_prompt
    assert "通常動画では冒頭30秒" not in short_prompt
    assert "通常動画では冒頭30秒" in standard_prompt
    assert "Shortsでは1秒目" not in standard_prompt


def test_default_idempotency_key_uses_current_prompt_version() -> None:
    assert f":{PROMPT_VERSION}:" in build_idempotency_key("topic-1")


def _fixed_script_payload(*, section_evidence_ids: list[str], narration: str) -> dict[str, Any]:
    return {
        "title_candidates": ["固定タイトル"],
        "target_audience": "初心者",
        "viewer_problem": "課題",
        "promised_outcome": "成果",
        "hook": "短いフック",
        "sections": [
            {
                "heading": "導入",
                "narration": narration,
                "visual_instruction": "背景を表示する。",
                "evidence_ids": section_evidence_ids,
                "dialogue": [],
            }
        ],
        "conclusion": "まとめ",
        "call_to_action": "登録してください",
        "description": "説明",
        "tags": ["tag"],
        "chapters": ["導入"],
    }


class _StubResultProvider:
    """operationごとに固定(または呼び出し回数に応じた)構造化データを返すテスト専用Provider。

    レスポンスが呼び出しごとに同一だと `call_llm` の `LLMCache` (input_hashが同一)
    により2回目以降は実プロバイダーを呼ばず即キャッシュ応答になってしまう。修復が
    複数回試みられることを検証するテストでは、呼び出し回数を受け取るcallableを渡して
    毎回わずかに異なる(それでも範囲外の)台本を返す。
    """

    def __init__(
        self, responses: dict[str, dict[str, Any] | Callable[[int], dict[str, Any]]]
    ) -> None:
        self._responses = responses
        self.calls: list[str] = []
        self._call_counts: dict[str, int] = {}

    async def generate_structured(
        self,
        *,
        operation: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[BaseModel],
        model_policy: str,
        idempotency_key: str,
    ) -> StructuredLLMResult:
        self.calls.append(operation)
        self._call_counts[operation] = self._call_counts.get(operation, 0) + 1
        response = self._responses[operation]
        data = response(self._call_counts[operation]) if callable(response) else response
        return StructuredLLMResult(
            data=data,
            model="stub-model",
            input_tokens=1,
            output_tokens=1,
            estimated_cost_micro_usd=0,
            latency_ms=1,
            cached=False,
        )


# 到底届かない目標尺にして、修復ループが必ず上限まで回るようにする。
_UNREACHABLE_SETTINGS = ProductionSettings(
    preset="custom",
    target_duration_seconds=3600,
    min_duration_seconds=3599,
    max_duration_seconds=3600,
)


def test_repair_is_attempted_up_to_max_twice_when_out_of_range(db_session: Session) -> None:
    topic = _make_topic(db_session)
    # 常に同じ短い台本を返す(evidence_idsは一貫させ、破棄条件には触れない)。
    fixed_payload = _fixed_script_payload(section_evidence_ids=["ev-1"], narration="短い本文")

    def _repair_payload(call_index: int) -> dict[str, Any]:
        # 呼び出しごとにわずかに異なる(それでも範囲外の)台本を返し、LLMCacheのヒットを避ける。
        return _fixed_script_payload(
            section_evidence_ids=["ev-1"], narration=f"短い本文{call_index}"
        )

    provider = _StubResultProvider(
        {
            "generate_script": fixed_payload,
            "repair_script_duration": _repair_payload,
        }
    )

    script = asyncio.run(
        generate_script(
            db_session,
            topic_id=topic.id,
            provider=provider,
            production_settings=_UNREACHABLE_SETTINGS,
        )
    )
    db_session.commit()

    assert provider.calls.count("repair_script_duration") == 2
    assert provider.calls[0] == "generate_script"
    # ベストエフォートで最後の(修復後の)内容が採用される。
    assert script.body["sections"][0]["evidence_ids"] == ["ev-1"]
    # Scriptの使用量集計は最後の修復1回分ではなく、初回+全修復の合計を保持する。
    assert script.input_tokens == 3
    assert script.output_tokens == 3


def test_repair_result_is_discarded_when_evidence_ids_are_lost(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    topic = _make_topic(db_session)
    original_payload = _fixed_script_payload(section_evidence_ids=["ev-1"], narration="短い本文")
    repaired_payload_without_evidence = _fixed_script_payload(
        section_evidence_ids=[], narration="短い本文"
    )
    provider = _StubResultProvider(
        {
            "generate_script": original_payload,
            "repair_script_duration": repaired_payload_without_evidence,
        }
    )

    script = asyncio.run(
        generate_script(
            db_session,
            topic_id=topic.id,
            provider=provider,
            production_settings=_UNREACHABLE_SETTINGS,
        )
    )
    db_session.commit()

    # evidence_idsが失われた修復結果は破棄され、1回だけ修復を試みて元versionを維持する。
    assert provider.calls.count("repair_script_duration") == 1
    assert script.body["sections"][0]["evidence_ids"] == ["ev-1"]


def test_repair_result_is_discarded_when_unknown_evidence_ids_are_added(
    db_session: Session,
) -> None:
    topic = _make_topic(db_session)
    original_payload = _fixed_script_payload(section_evidence_ids=["ev-1"], narration="短い本文")
    repaired_payload = _fixed_script_payload(
        section_evidence_ids=["ev-1", "unknown-evidence"], narration="短い本文"
    )
    provider = _StubResultProvider(
        {
            "generate_script": original_payload,
            "repair_script_duration": repaired_payload,
        }
    )

    script = asyncio.run(
        generate_script(
            db_session,
            topic_id=topic.id,
            provider=provider,
            production_settings=_UNREACHABLE_SETTINGS,
        )
    )

    assert provider.calls.count("repair_script_duration") == 1
    assert script.body["sections"][0]["evidence_ids"] == ["ev-1"]


def test_settings_checksum_changes_idempotency_key() -> None:
    checksum_a = ProductionSettings.from_preset("short").checksum()
    checksum_b = ProductionSettings.from_preset("standard_3min").checksum()

    key_a = build_idempotency_key("topic-1", "script_v3", checksum_a)
    key_b = build_idempotency_key("topic-1", "script_v3", checksum_b)

    assert key_a != key_b


def test_different_production_settings_trigger_regeneration(db_session: Session) -> None:
    """production_settingsが異なれば idempotency_key が変わり、再生成(新バージョン)される。"""
    topic = _make_topic(db_session)
    provider = DeterministicFakeLLMProvider()

    script1 = asyncio.run(
        generate_script(
            db_session,
            topic_id=topic.id,
            provider=provider,
            production_settings=ProductionSettings.from_preset("short"),
        )
    )
    db_session.commit()

    script2 = asyncio.run(
        generate_script(
            db_session,
            topic_id=topic.id,
            provider=provider,
            production_settings=ProductionSettings.from_preset("standard_3min"),
        )
    )
    db_session.commit()

    assert script1.id != script2.id
    assert script2.version == script1.version + 1

    # 古い設定で再度呼んだ場合、単に最新versionを返すのではなく、同じ設定checksumの
    # script1を正しく返す。
    script1_again = asyncio.run(
        generate_script(
            db_session,
            topic_id=topic.id,
            provider=provider,
            production_settings=ProductionSettings.from_preset("short"),
        )
    )
    assert script1_again.id == script1.id


def test_regeneration_key_forces_new_script_version(db_session: Session) -> None:
    topic = _make_topic(db_session)
    provider = DeterministicFakeLLMProvider()
    settings = ProductionSettings.from_preset("short")
    first = asyncio.run(
        generate_script(
            db_session, topic_id=topic.id, provider=provider, production_settings=settings
        )
    )
    db_session.commit()

    rebuilt = asyncio.run(
        generate_script(
            db_session,
            topic_id=topic.id,
            provider=provider,
            production_settings=settings,
            regeneration_key="replacement-project-id",
        )
    )

    assert rebuilt.id != first.id
    assert rebuilt.version == first.version + 1


class _CapturingFakeProvider:
    """Fake LLMへ委譲しつつ、operationごとのsystem promptを記録する。"""

    def __init__(self) -> None:
        self._delegate = DeterministicFakeLLMProvider()
        self.system_prompts: list[tuple[str, str]] = []

    async def generate_structured(
        self,
        *,
        operation: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[BaseModel],
        model_policy: str,
        idempotency_key: str,
    ) -> StructuredLLMResult:
        self.system_prompts.append((operation, system_prompt))
        return await self._delegate.generate_structured(
            operation=operation,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=response_schema,
            model_policy=model_policy,
            idempotency_key=idempotency_key,
        )


def _add_self_review_insights(
    db_session: Session, *, channel_id: str, recommended_actions: list[str]
) -> list[Insight]:
    prior_topic = Topic(
        channel_id=channel_id,
        title="過去動画",
        source_type="manual",
        source_ref="past-video-for-self-review",
    )
    db_session.add(prior_topic)
    db_session.flush()
    project = VideoProject(
        topic_id=prior_topic.id,
        status="UPLOADED_PRIVATE",
        generation=1,
    )
    db_session.add(project)
    db_session.flush()
    publication = Publication(
        video_project_id=project.id,
        youtube_video_id="past-video-id",
        title="過去動画",
        description="",
        privacy_status="private",
        idempotency_key="upload:past-video-for-self-review",
        upload_status="completed",
    )
    db_session.add(publication)
    db_session.flush()
    insights = [
        Insight(
            source_type="publication",
            source_id=publication.id,
            insight_type="self_review",
            source_ref=f"self_review:{publication.id}:2026-07-12:{index}",
            finding=f"改善点{index + 1}",
            evidence={"metric_date": "2026-07-12"},
            confidence=0.6,
            recommended_action=action,
            human_review_reason="次回台本へ自動反映",
        )
        for index, action in enumerate(recommended_actions)
    ]
    db_session.add_all(insights)
    db_session.flush()
    return insights


def _new_topic_for_channel(db_session: Session, *, channel_id: str, source_ref: str) -> Topic:
    topic = Topic(
        channel_id=channel_id,
        title="次回動画",
        description="セルフレビューを反映する次回企画",
        source_type="manual",
        source_ref=source_ref,
    )
    db_session.add(topic)
    db_session.flush()
    return topic


def _initial_system_prompt(provider: _CapturingFakeProvider) -> str:
    return next(
        prompt for operation, prompt in provider.system_prompts if operation == "generate_script"
    )


def _add_prior_performance(
    db_session: Session,
    *,
    channel_id: str,
    key: str,
    title: str,
    metric_values: list[tuple[date, float, float, int]],
    upload_status: str = "completed",
) -> None:
    prior_topic = Topic(
        channel_id=channel_id,
        title=title,
        source_type="manual",
        source_ref=f"performance-{key}",
    )
    db_session.add(prior_topic)
    db_session.flush()
    project = VideoProject(topic_id=prior_topic.id, status="UPLOADED_PRIVATE", generation=1)
    db_session.add(project)
    db_session.flush()
    publication = Publication(
        video_project_id=project.id,
        youtube_video_id=f"perf-{key}"[:32],
        title=title,
        description="",
        privacy_status="private",
        idempotency_key=f"upload:performance:{key}",
        upload_status=upload_status,
    )
    db_session.add(publication)
    db_session.flush()
    db_session.add_all(
        [
            VideoMetricDaily(
                publication_id=publication.id,
                metric_date=metric_date,
                ctr=ctr,
                average_view_percentage=average_view_percentage,
                subscribers_gained=subscribers_gained,
            )
            for metric_date, ctr, average_view_percentage, subscribers_gained in metric_values
        ]
    )
    db_session.flush()


def test_recent_performance_context_is_channel_scoped_latest_and_limited(
    db_session: Session,
) -> None:
    topic = _make_topic(db_session)
    other_channel = Channel(name="other-channel")
    db_session.add(other_channel)
    db_session.flush()
    _add_prior_performance(
        db_session,
        channel_id=topic.channel_id,
        key="oldest",
        title="対象外になる古い動画",
        metric_values=[(date(2026, 7, 1), 0.01, 0.20, 1)],
    )
    _add_prior_performance(
        db_session,
        channel_id=topic.channel_id,
        key="middle",
        title="中位の動画",
        metric_values=[(date(2026, 7, 2), 0.02, 0.30, 2)],
    )
    _add_prior_performance(
        db_session,
        channel_id=topic.channel_id,
        key="latest-metric",
        title="最新値を使う動画",
        metric_values=[
            (date(2026, 7, 3), 0.03, 0.40, 3),
            (date(2026, 7, 5), 0.15, 0.65, 15),
        ],
    )
    _add_prior_performance(
        db_session,
        channel_id=topic.channel_id,
        key="recent",
        title="新しい動画",
        metric_values=[(date(2026, 7, 4), 0.04, 0.50, 4)],
    )
    _add_prior_performance(
        db_session,
        channel_id=other_channel.id,
        key="foreign",
        title="別チャンネル動画",
        metric_values=[(date(2026, 7, 7), 0.99, 0.99, 99)],
    )
    _add_prior_performance(
        db_session,
        channel_id=topic.channel_id,
        key="incomplete",
        title="未完了投稿",
        metric_values=[(date(2026, 7, 6), 0.88, 0.88, 88)],
        upload_status="failed",
    )

    context = _build_recent_performance_context(
        db_session,
        channel_id=topic.channel_id,
        exclude_topic_id=topic.id,
    )

    assert "過去投稿実績(参考観測値)" in context
    assert "因果関係や次回の成果を保証するものではなく" in context
    assert context.count('- title="') == 3
    assert "最新値を使う動画" in context
    assert "CTR=15.00%" in context
    assert "平均視聴率=65.00%" in context
    assert "登録者増=15" in context
    assert "対象外になる古い動画" not in context
    assert "別チャンネル動画" not in context
    assert "未完了投稿" not in context


def test_recent_performance_context_is_empty_without_channel_or_metrics(
    db_session: Session,
) -> None:
    topic = _make_topic(db_session)

    assert _build_recent_performance_context(db_session, channel_id=None) == ""
    assert _build_recent_performance_context(db_session, channel_id=topic.channel_id) == ""


def test_generate_script_appends_self_review_lessons_at_prompt_end(
    db_session: Session,
) -> None:
    topic = _make_topic(db_session)
    action = "コード解説は1画面30秒以内に分割してください"
    _add_self_review_insights(db_session, channel_id=topic.channel_id, recommended_actions=[action])
    _add_prior_performance(
        db_session,
        channel_id=topic.channel_id,
        key="with-self-review",
        title="実績コンテキスト付き動画",
        metric_values=[(date(2026, 7, 13), 0.07, 0.55, 7)],
    )
    provider = _CapturingFakeProvider()

    asyncio.run(generate_script(db_session, topic_id=topic.id, provider=provider))

    prompt = _initial_system_prompt(provider)
    expected_block = f"【過去動画の振り返りからの改善指示(必ず反映)】\n- {action}"
    assert prompt.index("同一チャンネルの過去投稿実績") < prompt.index(
        "過去動画の振り返りからの改善指示"
    )
    assert prompt.endswith(expected_block)


def test_generate_script_injects_at_most_five_self_review_lessons(
    db_session: Session,
) -> None:
    topic = _make_topic(db_session)
    _add_self_review_insights(
        db_session,
        channel_id=topic.channel_id,
        recommended_actions=[f"改善指示{index}" for index in range(6)],
    )
    provider = _CapturingFakeProvider()

    asyncio.run(generate_script(db_session, topic_id=topic.id, provider=provider))

    prompt = _initial_system_prompt(provider)
    lesson_block = prompt.split("【過去動画の振り返りからの改善指示(必ず反映)】\n", 1)[1]
    assert len(lesson_block.splitlines()) == 5


def test_deleted_self_review_lesson_is_not_injected_into_new_topic(
    db_session: Session,
) -> None:
    base_topic = _make_topic(db_session)
    action = "削除後は注入されない改善指示"
    insight = _add_self_review_insights(
        db_session, channel_id=base_topic.channel_id, recommended_actions=[action]
    )[0]
    db_session.delete(insight)
    db_session.flush()
    new_topic = _new_topic_for_channel(
        db_session,
        channel_id=base_topic.channel_id,
        source_ref="new-topic-after-self-review-deletion",
    )
    provider = _CapturingFakeProvider()

    asyncio.run(generate_script(db_session, topic_id=new_topic.id, provider=provider))

    prompt = _initial_system_prompt(provider)
    assert "過去動画の振り返りからの改善指示" not in prompt
    assert action not in prompt
