from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel

from app.core.config import Settings
from app.providers.llm.base import LLMProvider, StructuredLLMResult
from app.providers.llm.factory import get_llm_provider
from app.providers.llm.fake import DeterministicFakeLLMProvider
from app.providers.llm.routing import RoutingLLMProvider


class _DummySchema(BaseModel):
    value: str


class _StubProvider:
    """テスト用の最小限のLLMProvider実装(実APIを呼ばない)。"""

    def __init__(self, model: str) -> None:
        self.model = model
        self.calls: list[str] = []

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
        self.calls.append(model_policy)
        return StructuredLLMResult(
            data={"value": self.model},
            model=self.model,
            input_tokens=1,
            output_tokens=1,
            estimated_cost_micro_usd=0,
            latency_ms=1,
            cached=False,
        )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_routing_provider_satisfies_protocol() -> None:
    low = _StubProvider("low-model")
    mid = _StubProvider("mid-model")
    high = _StubProvider("high-model")
    router = RoutingLLMProvider({"low": low, "mid": mid, "high": high})
    assert isinstance(router, LLMProvider)


def test_routing_provider_delegates_by_model_policy() -> None:
    low = _StubProvider("low-model")
    mid = _StubProvider("mid-model")
    high = _StubProvider("high-model")
    router = RoutingLLMProvider({"low": low, "mid": mid, "high": high})

    result = _run(
        router.generate_structured(
            operation="generate_script",
            system_prompt="sys",
            user_prompt="usr",
            response_schema=_DummySchema,
            model_policy="high",
            idempotency_key="key-1",
        )
    )

    assert result.model == "high-model"
    assert low.calls == []
    assert mid.calls == []
    assert high.calls == ["high"]


def test_routing_provider_requires_all_policies() -> None:
    with pytest.raises(ValueError):
        RoutingLLMProvider({"low": _StubProvider("low-model")})


def test_routing_provider_rejects_unknown_model_policy() -> None:
    router = RoutingLLMProvider(
        {
            "low": _StubProvider("low-model"),
            "mid": _StubProvider("mid-model"),
            "high": _StubProvider("high-model"),
        }
    )
    with pytest.raises(ValueError):
        _run(
            router.generate_structured(
                operation="generate_script",
                system_prompt="sys",
                user_prompt="usr",
                response_schema=_DummySchema,
                model_policy="unknown",
                idempotency_key="key-2",
            )
        )


def test_get_llm_provider_returns_single_instance_when_all_policies_match() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        LLM_PROVIDER="fake",
        LLM_PROVIDER_LOW="",
        LLM_PROVIDER_MID="",
        LLM_PROVIDER_HIGH="",
    )
    provider = get_llm_provider(settings)
    assert isinstance(provider, DeterministicFakeLLMProvider)


def test_get_llm_provider_returns_single_instance_when_explicitly_all_same() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        LLM_PROVIDER="fake",
        LLM_PROVIDER_LOW="fake",
        LLM_PROVIDER_MID="fake",
        LLM_PROVIDER_HIGH="fake",
    )
    provider = get_llm_provider(settings)
    assert isinstance(provider, DeterministicFakeLLMProvider)
    assert not isinstance(provider, RoutingLLMProvider)


def test_get_llm_provider_returns_routing_provider_when_policies_differ() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        LLM_PROVIDER="fake",
        LLM_PROVIDER_LOW="fake",
        LLM_PROVIDER_MID="fake",
        LLM_PROVIDER_HIGH="anthropic",
        ANTHROPIC_API_KEY="test-key-not-real",
    )
    provider = get_llm_provider(settings)
    assert isinstance(provider, RoutingLLMProvider)


def test_get_llm_provider_routes_local_for_low_mid_and_anthropic_for_high() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        LLM_PROVIDER="fake",
        LLM_PROVIDER_LOW="local",
        LLM_PROVIDER_MID="local",
        LLM_PROVIDER_HIGH="anthropic",
        ANTHROPIC_API_KEY="test-key-not-real",
    )
    provider = get_llm_provider(settings)
    assert isinstance(provider, RoutingLLMProvider)
    # low/midは同一LocalLLMProviderインスタンス、highは別のAnthropicLLMProviderインスタンス
    assert provider._providers_by_policy["low"] is provider._providers_by_policy["mid"]
    assert provider._providers_by_policy["low"] is not provider._providers_by_policy["high"]


def test_get_llm_provider_raises_for_unknown_provider_name() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        LLM_PROVIDER="not-a-real-provider",
    )
    with pytest.raises(ValueError):
        get_llm_provider(settings)


def test_get_llm_provider_raises_for_unknown_policy_provider_name() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        LLM_PROVIDER="fake",
        LLM_PROVIDER_HIGH="not-a-real-provider",
    )
    with pytest.raises(ValueError):
        get_llm_provider(settings)
