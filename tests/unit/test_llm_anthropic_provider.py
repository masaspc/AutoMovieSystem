from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.config import Settings
from app.providers.llm.anthropic import AnthropicLLMProvider
from app.providers.llm.base import LLMConfigurationError
from app.schemas.script_content import ScriptContent

_SETTINGS = Settings(_env_file=None, ANTHROPIC_API_KEY="test-key-not-real")  # type: ignore[call-arg]


def _script_tool_input() -> dict[str, Any]:
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


def _ok_response(model: str) -> httpx.Response:
    payload = {
        "id": "msg_test",
        "model": model,
        "content": [
            {"type": "tool_use", "name": "emit_structured_output", "input": _script_tool_input()}
        ],
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }
    return httpx.Response(200, json=payload, request=httpx.Request("POST", "https://x.invalid"))


def test_missing_api_key_raises_configuration_error() -> None:
    settings = Settings(_env_file=None, ANTHROPIC_API_KEY="")  # type: ignore[call-arg]
    with pytest.raises(LLMConfigurationError):
        AnthropicLLMProvider(settings)


def test_build_payload_uses_resolved_model_and_tool_schema() -> None:
    provider = AnthropicLLMProvider(_SETTINGS)
    payload = provider.build_payload(
        model="claude-sonnet-5",
        system_prompt="sys",
        user_prompt="usr",
        response_schema=ScriptContent,
    )

    assert payload["model"] == "claude-sonnet-5"
    assert payload["system"] == "sys"
    assert payload["messages"] == [{"role": "user", "content": "usr"}]
    assert payload["tool_choice"] == {"type": "tool", "name": "emit_structured_output"}
    assert payload["tools"][0]["name"] == "emit_structured_output"
    assert "title_candidates" in payload["tools"][0]["input_schema"]["properties"]


def test_generate_structured_sends_expected_request_and_parses_response() -> None:
    provider = AnthropicLLMProvider(_SETTINGS)
    mock_post = AsyncMock(return_value=_ok_response("claude-sonnet-5"))

    async def _run():  # type: ignore[no-untyped-def]
        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            return await provider.generate_structured(
                operation="generate_script",
                system_prompt="sys",
                user_prompt="usr",
                response_schema=ScriptContent,
                model_policy="mid",
                idempotency_key="key-1",
            )

    result = asyncio.run(_run())

    assert result.model == "claude-sonnet-5"
    assert result.input_tokens == 10
    assert result.output_tokens == 20
    assert result.estimated_cost_micro_usd > 0
    assert result.cached is False
    ScriptContent.model_validate(result.data)

    mock_post.assert_awaited_once()
    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["x-api-key"] == "test-key-not-real"
    assert kwargs["json"]["model"] == "claude-sonnet-5"


def test_generate_structured_retries_on_429_then_succeeds() -> None:
    provider = AnthropicLLMProvider(_SETTINGS)
    rate_limited = httpx.Response(
        429, json={"error": "rate limited"}, request=httpx.Request("POST", "https://x.invalid")
    )
    ok = _ok_response("claude-haiku-4-5-20251001")
    mock_post = AsyncMock(side_effect=[rate_limited, ok])

    async def _run():  # type: ignore[no-untyped-def]
        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            return await provider.generate_structured(
                operation="generate_script",
                system_prompt="sys",
                user_prompt="usr",
                response_schema=ScriptContent,
                model_policy="low",
                idempotency_key="key-2",
            )

    result = asyncio.run(_run())
    assert result.model == "claude-haiku-4-5-20251001"
    assert mock_post.await_count == 2


def test_generate_structured_gives_up_after_max_retries() -> None:
    provider = AnthropicLLMProvider(_SETTINGS)
    server_error = httpx.Response(
        503, json={"error": "unavailable"}, request=httpx.Request("POST", "https://x.invalid")
    )
    mock_post = AsyncMock(return_value=server_error)

    async def _run():  # type: ignore[no-untyped-def]
        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            await provider.generate_structured(
                operation="generate_script",
                system_prompt="sys",
                user_prompt="usr",
                response_schema=ScriptContent,
                model_policy="low",
                idempotency_key="key-3",
            )

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(_run())
    assert mock_post.await_count == 3
