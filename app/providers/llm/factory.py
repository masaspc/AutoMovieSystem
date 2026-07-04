"""LLMプロバイダーの選択(設定 `LLM_PROVIDER=fake|anthropic`: D-006)。"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.providers.llm.anthropic import AnthropicLLMProvider
from app.providers.llm.base import LLMProvider
from app.providers.llm.fake import DeterministicFakeLLMProvider


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    """設定に基づきLLMプロバイダーのインスタンスを返す。"""
    settings = settings or get_settings()
    if settings.LLM_PROVIDER == "fake":
        return DeterministicFakeLLMProvider()
    if settings.LLM_PROVIDER == "anthropic":
        return AnthropicLLMProvider(settings)
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.LLM_PROVIDER!r}")
