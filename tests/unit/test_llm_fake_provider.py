from __future__ import annotations

import asyncio

from app.providers.llm.fake import DeterministicFakeLLMProvider
from app.schemas.script_content import ScriptContent


def test_fake_provider_is_deterministic_for_same_input() -> None:
    provider = DeterministicFakeLLMProvider()

    async def _call() -> tuple:
        r1 = await provider.generate_structured(
            operation="generate_script",
            system_prompt="system",
            user_prompt="user",
            response_schema=ScriptContent,
            model_policy="mid",
            idempotency_key="generate_script:t-1:script_v1",
        )
        r2 = await provider.generate_structured(
            operation="generate_script",
            system_prompt="system",
            user_prompt="user",
            response_schema=ScriptContent,
            model_policy="mid",
            idempotency_key="generate_script:t-1:script_v1",
        )
        return r1, r2

    r1, r2 = asyncio.run(_call())

    assert r1.data == r2.data
    assert r1.model == r2.model
    assert r1.input_tokens == r2.input_tokens
    assert r1.output_tokens == r2.output_tokens
    assert r1.estimated_cost_micro_usd == r2.estimated_cost_micro_usd
    assert r1.latency_ms == r2.latency_ms
    assert r1.cached is False


def test_fake_provider_differs_for_different_input() -> None:
    provider = DeterministicFakeLLMProvider()

    async def _call() -> tuple:
        r1 = await provider.generate_structured(
            operation="generate_script",
            system_prompt="system",
            user_prompt="user A",
            response_schema=ScriptContent,
            model_policy="mid",
            idempotency_key="generate_script:t-1:script_v1",
        )
        r2 = await provider.generate_structured(
            operation="generate_script",
            system_prompt="system",
            user_prompt="user B",
            response_schema=ScriptContent,
            model_policy="mid",
            idempotency_key="generate_script:t-2:script_v1",
        )
        return r1, r2

    r1, r2 = asyncio.run(_call())

    assert r1.data != r2.data


def test_fake_provider_generate_script_matches_schema() -> None:
    provider = DeterministicFakeLLMProvider()

    async def _call():  # type: ignore[no-untyped-def]
        return await provider.generate_structured(
            operation="generate_script",
            system_prompt="system",
            user_prompt="user",
            response_schema=ScriptContent,
            model_policy="mid",
            idempotency_key="generate_script:t-1:script_v1",
        )

    result = asyncio.run(_call())
    content = ScriptContent.model_validate(result.data)

    assert content.title_candidates
    assert content.hook
    assert content.sections
    assert all(section.narration for section in content.sections)
    assert result.estimated_cost_micro_usd >= 0
