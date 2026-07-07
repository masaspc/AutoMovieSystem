"""Fake/Anthropic/Local 全てが LLMProvider Protocol の契約を満たすことを検証する。

実APIは呼ばない。Anthropic/Localはhttpxをモックする。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.config import Settings
from app.providers.llm.anthropic import AnthropicLLMProvider
from app.providers.llm.base import LLMProvider, StructuredLLMResult
from app.providers.llm.fake import DeterministicFakeLLMProvider
from app.providers.llm.local_openai import LocalLLMProvider
from app.schemas.script_content import ScriptContent


def _make_anthropic_response(model: str, tool_input: dict[str, Any]) -> httpx.Response:
    payload = {
        "id": "msg_test",
        "model": model,
        "content": [{"type": "tool_use", "name": "emit_structured_output", "input": tool_input}],
        "usage": {"input_tokens": 42, "output_tokens": 84},
    }
    return httpx.Response(
        200, json=payload, request=httpx.Request("POST", "https://example.invalid")
    )


def _make_local_response(model: str, content_data: dict[str, Any]) -> httpx.Response:
    payload = {
        "id": "chatcmpl-test",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(content_data)},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 34},
    }
    return httpx.Response(
        200, json=payload, request=httpx.Request("POST", "https://example.invalid")
    )


def _fake_script_data() -> dict:
    return {
        "title_candidates": ["タイトル案"],
        "target_audience": "対象視聴者",
        "viewer_problem": "課題",
        "promised_outcome": "得られる成果",
        "hook": "フック",
        "sections": [
            {
                "heading": "導入",
                "narration": "本編です。",
                "visual_instruction": "映像指示",
                "evidence_ids": [],
            }
        ],
        "conclusion": "まとめ",
        "call_to_action": "行動喚起",
        "description": "説明",
        "tags": ["tag1"],
        "chapters": ["導入"],
    }


@pytest.mark.contract
@pytest.mark.parametrize("provider_name", ["fake", "anthropic", "local"])
def test_provider_satisfies_llm_protocol(provider_name: str) -> None:
    if provider_name == "fake":
        provider: LLMProvider = DeterministicFakeLLMProvider()
    elif provider_name == "anthropic":
        settings = Settings(_env_file=None, ANTHROPIC_API_KEY="test-key-not-real")  # type: ignore[call-arg]
        provider = AnthropicLLMProvider(settings)
    else:
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        provider = LocalLLMProvider(settings)

    assert isinstance(provider, LLMProvider)


@pytest.mark.contract
def test_fake_and_anthropic_return_same_result_type() -> None:
    fake_provider = DeterministicFakeLLMProvider()

    async def _call_fake() -> StructuredLLMResult:
        return await fake_provider.generate_structured(
            operation="generate_script",
            system_prompt="system",
            user_prompt="user",
            response_schema=ScriptContent,
            model_policy="mid",
            idempotency_key="key-1",
        )

    fake_result = asyncio.run(_call_fake())
    assert isinstance(fake_result, StructuredLLMResult)
    assert isinstance(fake_result.data, dict)

    settings = Settings(_env_file=None, ANTHROPIC_API_KEY="test-key-not-real")  # type: ignore[call-arg]
    anthropic_provider = AnthropicLLMProvider(settings)

    async def _call_anthropic() -> StructuredLLMResult:
        mock_response = _make_anthropic_response("claude-sonnet-5", _fake_script_data())
        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_response)):
            return await anthropic_provider.generate_structured(
                operation="generate_script",
                system_prompt="system",
                user_prompt="user",
                response_schema=ScriptContent,
                model_policy="mid",
                idempotency_key="key-1",
            )

    anthropic_result = asyncio.run(_call_anthropic())
    assert isinstance(anthropic_result, StructuredLLMResult)
    assert isinstance(anthropic_result.data, dict)
    assert type(fake_result) is type(anthropic_result)

    local_settings = Settings(_env_file=None)  # type: ignore[call-arg]
    local_provider = LocalLLMProvider(local_settings)

    async def _call_local() -> StructuredLLMResult:
        mock_response = _make_local_response("qwen3:32b", _fake_script_data())
        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_response)):
            return await local_provider.generate_structured(
                operation="generate_script",
                system_prompt="system",
                user_prompt="user",
                response_schema=ScriptContent,
                model_policy="mid",
                idempotency_key="key-1",
            )

    local_result = asyncio.run(_call_local())
    assert isinstance(local_result, StructuredLLMResult)
    assert isinstance(local_result.data, dict)
    assert local_result.estimated_cost_micro_usd == 0
    assert type(fake_result) is type(local_result)


@pytest.mark.contract
def test_local_missing_response_content_raises_value_error() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    provider = LocalLLMProvider(settings)

    async def _call() -> StructuredLLMResult:
        payload = {"id": "chatcmpl-test", "model": "qwen3:8b", "choices": []}
        mock_response = httpx.Response(
            200, json=payload, request=httpx.Request("POST", "https://example.invalid")
        )
        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_response)):
            return await provider.generate_structured(
                operation="generate_script",
                system_prompt="system",
                user_prompt="user",
                response_schema=ScriptContent,
                model_policy="low",
                idempotency_key="key-2",
            )

    with pytest.raises(ValueError):
        asyncio.run(_call())


@pytest.mark.contract
def test_anthropic_missing_api_key_raises_configuration_error() -> None:
    from app.providers.llm.base import LLMConfigurationError

    settings = Settings(_env_file=None, ANTHROPIC_API_KEY="")  # type: ignore[call-arg]
    with pytest.raises(LLMConfigurationError):
        AnthropicLLMProvider(settings)
