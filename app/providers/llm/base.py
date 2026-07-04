"""LLMプロバイダー共通の型・Protocol・料金表(仕様§9)。

外部LLM API(Anthropic等)は本モジュールの `LLMProvider` Protocol を実装する形で
`app/providers/llm/` 配下に抽象化する。テストでは実APIを呼ばず、
`fake.py` の `DeterministicFakeLLMProvider` を使う。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from app.core.config import Settings

MODEL_POLICIES = ("low", "mid", "high")


@dataclass(frozen=True)
class ModelPricing:
    """モデルの100万トークンあたりの単価(マイクロUSD)。整数のみ(ADR-0007: float禁止)。"""

    input_micro_usd_per_million: int
    output_micro_usd_per_million: int


# モデルID -> 単価。実際の価格改定に追従する必要はあるが、金額は必ず整数マイクロUSD。
MODEL_PRICING: dict[str, ModelPricing] = {
    "claude-haiku-4-5-20251001": ModelPricing(
        input_micro_usd_per_million=800_000, output_micro_usd_per_million=4_000_000
    ),
    "claude-sonnet-5": ModelPricing(
        input_micro_usd_per_million=3_000_000, output_micro_usd_per_million=15_000_000
    ),
    "claude-opus-4-8": ModelPricing(
        input_micro_usd_per_million=15_000_000, output_micro_usd_per_million=75_000_000
    ),
}
# 未知のモデルID(Fakeプロバイダー等)向けのフォールバック単価。
_DEFAULT_PRICING = ModelPricing(
    input_micro_usd_per_million=1_000_000, output_micro_usd_per_million=5_000_000
)


def get_model_pricing(model: str) -> ModelPricing:
    """モデルIDから単価を引く。未知のモデルはデフォルト単価にフォールバックする。"""
    return MODEL_PRICING.get(model, _DEFAULT_PRICING)


def resolve_model_id(model_policy: str, settings: Settings) -> str:
    """model_policy ("low"|"mid"|"high") から実際のモデルIDを解決する(設定値)。"""
    mapping = {
        "low": settings.LLM_MODEL_LOW,
        "mid": settings.LLM_MODEL_MID,
        "high": settings.LLM_MODEL_HIGH,
    }
    if model_policy not in mapping:
        raise ValueError(
            f"Unknown model_policy: {model_policy!r} (expected one of {MODEL_POLICIES})"
        )
    return mapping[model_policy]


class LLMProviderError(Exception):
    """LLMプロバイダー共通エラー基底。"""


class LLMConfigurationError(LLMProviderError):
    """APIキー未設定等、プロバイダーの設定不備による初期化エラー。"""


class LLMResponseSchemaError(LLMProviderError):
    """構造化出力が指定スキーマに適合しない場合(修復リトライ後も失敗)。"""


@dataclass(frozen=True)
class StructuredLLMResult:
    """`LLMProvider.generate_structured` の戻り値。"""

    data: dict
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_micro_usd: int
    latency_ms: int
    cached: bool = False


@runtime_checkable
class LLMProvider(Protocol):
    """LLMプロバイダー共通インターフェース(仕様§9)。

    実装は `app/providers/llm/` 配下のみ(fake.py / anthropic.py)。
    呼び出し側は必ず `app/services/llm_gateway.py` 経由で使うこと。
    """

    async def generate_structured(
        self,
        *,
        operation: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[BaseModel],
        model_policy: str,
        idempotency_key: str,
    ) -> StructuredLLMResult: ...
