"""OpenAI互換 Chat Completions API 経由のローカルLLMプロバイダー(仕様§9・D-020)。

Ollama / LM Studio / vLLM 等、OpenAI互換の `POST {base_url}/chat/completions` を話す
ローカル推論サーバーを想定する。`response_format={"type": "json_object"}` と、
システムプロンプトへの JSON Schema(`response_schema.model_json_schema()`)埋め込みで
構造化出力を強制する。スキーマ不適合の修復リトライは既存 `app/services/llm_gateway.py`
に任せる(本プロバイダー自身は結果を検証しない。anthropic.py と同じ契約)。

コストは常に0(ローカル実行のためAPI課金なし)。接続エラー/5xxはtenacityで指数
バックオフ最大3回。テストでは実サーバーを呼ばない。httpxをモックしてリクエスト
構築のみ検証する。
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from pydantic import BaseModel
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import Settings
from app.providers.llm.base import StructuredLLMResult

_MAX_RETRIES = 3


def _is_retryable_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


def resolve_local_model_id(model_policy: str, settings: Settings) -> str:
    """model_policy ("low"|"mid"|"high") からローカルLLMのモデル名を解決する(設定値)。"""
    mapping = {
        "low": settings.LOCAL_LLM_MODEL_LOW,
        "mid": settings.LOCAL_LLM_MODEL_MID,
        "high": settings.LOCAL_LLM_MODEL_HIGH,
    }
    if model_policy not in mapping:
        raise ValueError(
            f"Unknown model_policy: {model_policy!r} (expected one of {tuple(mapping)})"
        )
    return mapping[model_policy]


class LocalLLMProvider:
    """`LLMProvider` Protocol を満たすOpenAI互換ローカルLLM実装。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.LOCAL_LLM_BASE_URL.rstrip("/")
        self._api_key = settings.LOCAL_LLM_API_KEY
        self._timeout_seconds = settings.LOCAL_LLM_TIMEOUT_SECONDS

    def build_payload(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[BaseModel],
    ) -> dict[str, Any]:
        """リクエストボディの構築(テストで検証しやすいよう公開メソッドにする)。"""
        schema_json = json.dumps(response_schema.model_json_schema(), ensure_ascii=False)
        system_with_schema = (
            f"{system_prompt}\n\n"
            "以下のJSON Schemaに厳密に従うJSONのみを出力してください。"
            "説明文やMarkdownのコードブロック記法は一切含めないこと。\n"
            f"{schema_json}"
        )
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": system_with_schema},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
        }

    def _build_headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

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
        model = resolve_local_model_id(model_policy, self._settings)
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

        content = self._extract_content(response_json)
        data = self._parse_json_content(content)

        usage = response_json.get("usage") or {}
        if "prompt_tokens" in usage and "completion_tokens" in usage:
            input_tokens = int(usage["prompt_tokens"])
            output_tokens = int(usage["completion_tokens"])
        else:
            # usageが返らないローカルサーバー向けの文字数からの概算(fake.pyと同じ流儀)。
            input_tokens = max(1, (len(system_prompt) + len(user_prompt)) // 2)
            output_tokens = max(1, len(content) // 2)

        return StructuredLLMResult(
            data=data,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            # ローカル実行はAPI課金が発生しないため常に0。
            estimated_cost_micro_usd=0,
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
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions", json=payload, headers=headers
                )
                response.raise_for_status()
                result: dict[str, Any] = response.json()
                return result

        return await _call()

    @staticmethod
    def _extract_content(response_json: dict[str, Any]) -> str:
        choices = response_json.get("choices") or []
        if not choices:
            raise ValueError("local LLM response did not contain any choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise ValueError("local LLM response message did not contain string content")
        return content

    @staticmethod
    def _parse_json_content(content: str) -> dict[str, Any]:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"local LLM response content was not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("local LLM response content did not decode to a JSON object")
        return data
