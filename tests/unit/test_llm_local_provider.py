from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.providers.llm.local_openai import LocalLLMProvider
from app.schemas.script_content import ScriptContent

_SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]


def _script_data() -> dict[str, Any]:
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


def _ok_response(
    model: str, *, content: str | None = None, usage: dict[str, int] | None = None
) -> httpx.Response:
    payload: dict[str, Any] = {
        "id": "chatcmpl-test",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content if content is not None else json.dumps(_script_data()),
                },
                "finish_reason": "stop",
            }
        ],
    }
    if usage is not None:
        payload["usage"] = usage
    return httpx.Response(200, json=payload, request=httpx.Request("POST", "https://x.invalid"))


def test_build_payload_uses_resolved_model_json_object_and_schema() -> None:
    provider = LocalLLMProvider(_SETTINGS)
    payload = provider.build_payload(
        model="qwen3:32b",
        system_prompt="sys",
        user_prompt="usr",
        response_schema=ScriptContent,
    )

    assert payload["model"] == "qwen3:32b"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][1] == {"role": "user", "content": "usr"}
    system_message = payload["messages"][0]
    assert system_message["role"] == "system"
    assert "sys" in system_message["content"]
    assert "title_candidates" in system_message["content"]


def test_generate_structured_sends_expected_request_and_parses_response() -> None:
    provider = LocalLLMProvider(_SETTINGS)
    mock_post = AsyncMock(
        return_value=_ok_response("qwen3:32b", usage={"prompt_tokens": 12, "completion_tokens": 34})
    )

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

    assert result.model == "qwen3:32b"
    assert result.input_tokens == 12
    assert result.output_tokens == 34
    assert result.estimated_cost_micro_usd == 0
    assert result.cached is False
    ScriptContent.model_validate(result.data)

    mock_post.assert_awaited_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "http://localhost:11434/v1/chat/completions"
    assert "Authorization" not in kwargs["headers"]
    assert kwargs["json"]["model"] == "qwen3:32b"


def test_generate_structured_sends_bearer_header_when_api_key_configured() -> None:
    settings = Settings(_env_file=None, LOCAL_LLM_API_KEY="local-secret-not-real")  # type: ignore[call-arg]
    provider = LocalLLMProvider(settings)
    mock_post = AsyncMock(return_value=_ok_response("qwen3:8b"))

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

    asyncio.run(_run())

    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer local-secret-not-real"


def test_generate_structured_estimates_tokens_when_usage_missing() -> None:
    provider = LocalLLMProvider(_SETTINGS)
    mock_post = AsyncMock(return_value=_ok_response("qwen3:32b", usage=None))

    async def _run():  # type: ignore[no-untyped-def]
        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            return await provider.generate_structured(
                operation="generate_script",
                system_prompt="s" * 10,
                user_prompt="u" * 20,
                response_schema=ScriptContent,
                model_policy="mid",
                idempotency_key="key-3",
            )

    result = asyncio.run(_run())

    assert result.input_tokens == max(1, (10 + 20) // 2)
    assert result.output_tokens > 0
    assert result.estimated_cost_micro_usd == 0


def test_generate_structured_retries_on_503_then_succeeds() -> None:
    provider = LocalLLMProvider(_SETTINGS)
    server_error = httpx.Response(
        503, json={"error": "unavailable"}, request=httpx.Request("POST", "https://x.invalid")
    )
    ok = _ok_response("qwen3:8b")
    mock_post = AsyncMock(side_effect=[server_error, ok])

    async def _run():  # type: ignore[no-untyped-def]
        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            return await provider.generate_structured(
                operation="generate_script",
                system_prompt="sys",
                user_prompt="usr",
                response_schema=ScriptContent,
                model_policy="low",
                idempotency_key="key-4",
            )

    result = asyncio.run(_run())
    assert result.model == "qwen3:8b"
    assert mock_post.await_count == 2


def test_generate_structured_gives_up_after_max_retries() -> None:
    provider = LocalLLMProvider(_SETTINGS)
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
                idempotency_key="key-5",
            )

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(_run())
    assert mock_post.await_count == 3


def test_generate_structured_raises_value_error_on_malformed_json_content() -> None:
    provider = LocalLLMProvider(_SETTINGS)
    mock_post = AsyncMock(return_value=_ok_response("qwen3:8b", content="not valid json"))

    async def _run():  # type: ignore[no-untyped-def]
        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            await provider.generate_structured(
                operation="generate_script",
                system_prompt="sys",
                user_prompt="usr",
                response_schema=ScriptContent,
                model_policy="low",
                idempotency_key="key-6",
            )

    with pytest.raises(ValueError, match="not valid JSON"):
        asyncio.run(_run())


def test_generate_structured_returns_unvalidated_data_on_schema_mismatch() -> None:
    """スキーマ不適合の修復リトライはllm_gateway側の責務。プロバイダーは検証しない。"""
    provider = LocalLLMProvider(_SETTINGS)
    mismatched = json.dumps({"unexpected": "shape"})
    mock_post = AsyncMock(return_value=_ok_response("qwen3:8b", content=mismatched))

    async def _run():  # type: ignore[no-untyped-def]
        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            return await provider.generate_structured(
                operation="generate_script",
                system_prompt="sys",
                user_prompt="usr",
                response_schema=ScriptContent,
                model_policy="low",
                idempotency_key="key-7",
            )

    result = asyncio.run(_run())
    assert result.data == {"unexpected": "shape"}
    with pytest.raises(ValidationError):
        ScriptContent.model_validate(result.data)
