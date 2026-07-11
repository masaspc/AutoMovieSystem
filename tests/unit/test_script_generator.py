from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.channel import Channel
from app.models.evidence import Evidence
from app.models.script import Script
from app.models.topic import Topic
from app.models.usage_record import UsageRecord
from app.providers.llm.base import StructuredLLMResult
from app.providers.llm.fake import DeterministicFakeLLMProvider
from app.schemas.production_settings import ProductionSettings
from app.services.scripts.generator import _build_prompts, build_idempotency_key, generate_script


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
