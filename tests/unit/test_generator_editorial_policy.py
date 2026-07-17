from __future__ import annotations

import asyncio

from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.script import Script
from app.models.topic import Topic
from app.providers.llm.fake import DeterministicFakeLLMProvider
from app.schemas.editorial_policy import ChannelEditorialPolicy
from app.schemas.production_settings import ProductionSettings
from app.services.scripts.generator import _build_prompts, generate_script
from app.services.scripts.variety import pick_variety_plan


def _topic(db_session: Session, *, policy: dict | None) -> tuple[Channel, Topic]:
    channel = Channel(name="policy-channel", editorial_policy=policy)
    db_session.add(channel)
    db_session.flush()
    topic = Topic(
        channel_id=channel.id,
        title="NISA入門",
        source_type="manual",
        source_ref="policy-test",
    )
    db_session.add(topic)
    db_session.flush()
    return channel, topic


def test_policy_prompt_is_optional_and_precedes_variety_instructions(
    db_session: Session,
) -> None:
    _channel, topic = _topic(db_session, policy=None)
    settings = ProductionSettings(script_template="explainer")
    variety = pick_variety_plan(topic.id, template=settings.script_template)
    without_policy, _ = _build_prompts(topic, [], settings, variety)
    assert "チャンネル編集方針" not in without_policy

    policy = ChannelEditorialPolicy(
        tone="落ち着いた丁寧解説",
        target_audience="投資未経験者",
        prohibited_instructions=["個別銘柄を推奨しない", "売買タイミングを助言しない"],
    )
    with_policy, _ = _build_prompts(topic, [], settings, variety, policy)
    assert "落ち着いた丁寧解説" in with_policy
    assert "投資未経験者" in with_policy
    assert "個別銘柄を推奨しない" in with_policy
    assert with_policy.index("チャンネル編集方針") < with_policy.index(
        "今回の演出バリエーション"
    )


def test_policy_change_changes_generation_key_and_manifest(db_session: Session) -> None:
    channel, topic = _topic(
        db_session,
        policy={"tone": "丁寧", "prohibited_instructions": ["銘柄を推奨しない"]},
    )
    provider = DeterministicFakeLLMProvider()
    first = asyncio.run(
        generate_script(db_session, topic_id=topic.id, provider=provider)
    )
    channel.editorial_policy = {
        "tone": "簡潔",
        "prohibited_instructions": ["銘柄を推奨しない"],
    }
    db_session.flush()
    second = asyncio.run(
        generate_script(db_session, topic_id=topic.id, provider=provider)
    )

    assert first.id != second.id
    assert db_session.query(Script).filter(Script.topic_id == topic.id).count() == 2
    assert first.source_manifest["editorial_policy_checksum"] != second.source_manifest[
        "editorial_policy_checksum"
    ]
    assert first.source_manifest["generation_settings_checksum"] != second.source_manifest[
        "generation_settings_checksum"
    ]
    assert second.source_manifest["editorial_policy"]["tone"] == "簡潔"
