"""Anthropic Messages API 経由の構造化LLM出力プロバイダー(仕様§9)。

tool_use を用いてJSON構造化出力を強制する。429/5xxはtenacityで指数バックオフ
最大3回リトライする。APIキー未設定なら初期化時に明示エラー(`LLMConfigurationError`)。

テストでは実APIを呼ばない。httpxをモックしてリクエスト構築のみ検証する。
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from pydantic import BaseModel
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import Settings
from app.providers.llm.base import (
    LLMConfigurationError,
    StructuredLLMResult,
    get_model_pricing,
    resolve_model_id,
)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
_TOOL_NAME = "emit_structured_output"
_MAX_RETRIES = 3
_MAX_OUTPUT_TOKENS = 4096
_TIMEOUT_SECONDS = 60.0


def _is_retryable_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return isinstance(exc, httpx.TransportError)


class AnthropicLLMProvider:
    """`LLMProvider` Protocol を満たすAnthropic実装。"""

    def __init__(self, settings: Settings) -> None:
        if not settings.ANTHROPIC_API_KEY:
            raise LLMConfigurationError("ANTHROPIC_API_KEY is not configured")
        self._settings = settings
        self._api_key = settings.ANTHROPIC_API_KEY

    def _build_tool(self, response_schema: type[BaseModel]) -> dict[str, Any]:
        return {
            "name": _TOOL_NAME,
            "description": "指定されたJSON Schemaに厳密に適合する構造化データを返す。",
            "input_schema": response_schema.model_json_schema(),
        }

    def build_payload(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[BaseModel],
    ) -> dict[str, Any]:
        """リクエストボディの構築(テストで検証しやすいよう公開メソッドにする)。"""
        tool = self._build_tool(response_schema)
        return {
            "model": model,
            "max_tokens": _MAX_OUTPUT_TOKENS,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": tool["name"]},
        }

    def _build_headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        }

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
        model = resolve_model_id(model_policy, self._settings)
        payload = self.build_payload(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=response_schema,
        )
        headers = self._build_headers()

        start = time.monotonic()
        response_json = await self._post_with_retry(payload, headers)
        latency_ms = int((time.monotonic() - start) * 1000)

        data = self._extract_tool_input(response_json)
        usage = response_json.get("usage", {})
        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        pricing = get_model_pricing(model)
        cost = (
            input_tokens * pricing.input_micro_usd_per_million
            + output_tokens * pricing.output_micro_usd_per_million
        ) // 1_000_000

        return StructuredLLMResult(
            data=data,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_micro_usd=cost,
            latency_ms=latency_ms,
            cached=False,
        )

    async def _post_with_retry(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        @retry(
            retry=retry_if_exception(_is_retryable_error),
            stop=stop_after_attempt(_MAX_RETRIES),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            reraise=True,
        )
        async def _call() -> dict[str, Any]:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(ANTHROPIC_API_URL, json=payload, headers=headers)
                response.raise_for_status()
                result: dict[str, Any] = response.json()
                return result

        return await _call()

    @staticmethod
    def _extract_tool_input(response_json: dict[str, Any]) -> dict[str, Any]:
        for block in response_json.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == _TOOL_NAME:
                result: dict[str, Any] = block.get("input", {})
                return result
        raise ValueError("Anthropic response did not contain expected tool_use block")
