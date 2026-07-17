from __future__ import annotations

import asyncio

from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.topic import Topic
from app.providers.llm.fake import DeterministicFakeLLMProvider
from app.schemas.production_settings import ProductionSettings
from app.services.scripts.generator import PROMPT_VERSION, _build_prompts, generate_script
from app.services.scripts.variety import pick_variety_plan


def _topic(db_session: Session) -> Topic:
    channel = Channel(name="variety-channel")
    db_session.add(channel)
    db_session.flush()
    topic = Topic(
        channel_id=channel.id,
        title="Python入門",
        source_type="manual",
        source_ref="variety-test",
    )
    db_session.add(topic)
    db_session.flush()
    return topic


def test_prompt_contains_selected_variety_and_shared_pattern_instructions(
    db_session: Session,
) -> None:
    topic = _topic(db_session)
    settings = ProductionSettings(script_template="explainer")
    plan = pick_variety_plan(topic.id, template=settings.script_template)

    system_prompt, _ = _build_prompts(topic, [], settings, plan)

    assert "今回の演出バリエーション" in system_prompt
    assert plan.hook_style in system_prompt
    assert "2〜3分ごと" in system_prompt
    assert "問いかけを、台本内に必ず1箇所以上" in system_prompt


def test_generate_script_records_variety_plan_in_source_manifest(db_session: Session) -> None:
    topic = _topic(db_session)
    settings = ProductionSettings(script_template="news_commentary")

    script = asyncio.run(
        generate_script(
            db_session,
            topic_id=topic.id,
            provider=DeterministicFakeLLMProvider(),
            production_settings=settings,
        )
    )

    expected = pick_variety_plan(topic.id, template="news_commentary")
    recorded = script.source_manifest["variety_plan"]
    assert recorded == {
        "hook_style": expected.hook_style,
        "corners": expected.corners,
        "tsukkomi_density": expected.tsukkomi_density,
        "bridge_style": expected.bridge_style,
    }
    assert script.prompt_version == PROMPT_VERSION == "script_v8_variety"
