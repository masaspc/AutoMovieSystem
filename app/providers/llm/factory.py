"""LLMプロバイダーの選択(設定 `LLM_PROVIDER=fake|anthropic|local`: D-006・D-020)。

model_policy("low"/"mid"/"high")別に異なるプロバイダーを使いたい場合は
`LLM_PROVIDER_LOW`/`LLM_PROVIDER_MID`/`LLM_PROVIDER_HIGH` を設定する(空文字なら
`LLM_PROVIDER` に従う)。全ポリシーが同一プロバイダー名に解決される場合は従来どおり
単一プロバイダーを返し、異なる場合のみ `RoutingLLMProvider` でラップする。
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.providers.llm.anthropic import AnthropicLLMProvider
from app.providers.llm.base import LLMProvider
from app.providers.llm.fake import DeterministicFakeLLMProvider
from app.providers.llm.local_openai import LocalLLMProvider
from app.providers.llm.routing import RoutingLLMProvider

_VALID_PROVIDER_NAMES = ("fake", "anthropic", "local")


def _build_provider(name: str, settings: Settings) -> LLMProvider:
    if name == "fake":
        return DeterministicFakeLLMProvider()
    if name == "anthropic":
        return AnthropicLLMProvider(settings)
    if name == "local":
        return LocalLLMProvider(settings)
    raise ValueError(f"Unknown LLM provider: {name!r} (expected one of {_VALID_PROVIDER_NAMES})")


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    """設定に基づきLLMプロバイダーのインスタンスを返す。"""
    settings = settings or get_settings()

    policy_provider_names = {
        "low": settings.LLM_PROVIDER_LOW or settings.LLM_PROVIDER,
        "mid": settings.LLM_PROVIDER_MID or settings.LLM_PROVIDER,
        "high": settings.LLM_PROVIDER_HIGH or settings.LLM_PROVIDER,
    }
    unique_names = set(policy_provider_names.values())

    if len(unique_names) == 1:
        return _build_provider(next(iter(unique_names)), settings)

    instances_by_name = {name: _build_provider(name, settings) for name in unique_names}
    providers_by_policy = {
        policy: instances_by_name[name] for policy, name in policy_provider_names.items()
    }
    return RoutingLLMProvider(providers_by_policy)
