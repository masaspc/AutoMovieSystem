"""TrendProvider(fake/factory)の検証。実ネットワークは使わない。"""

from __future__ import annotations

import asyncio

from app.core.config import Settings
from app.providers.trends.base import TrendItem, TrendProvider
from app.providers.trends.factory import get_trend_provider
from app.providers.trends.fake import FakeTrendProvider


def test_fake_provider_returns_deterministic_items() -> None:
    provider = FakeTrendProvider()
    first = asyncio.run(provider.fetch_latest(limit=3))
    second = asyncio.run(provider.fetch_latest(limit=3))
    assert first == second
    assert len(first) == 3
    assert all(isinstance(item, TrendItem) for item in first)
    assert all(item.url.startswith("https://") for item in first)


def test_fake_provider_respects_limit() -> None:
    provider = FakeTrendProvider()
    items = asyncio.run(provider.fetch_latest(limit=1))
    assert len(items) == 1


def test_factory_returns_fake_by_default() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    provider = get_trend_provider(settings)
    assert isinstance(provider, FakeTrendProvider)
    assert isinstance(provider, TrendProvider)


def test_factory_rejects_unknown_provider() -> None:
    import pytest

    settings = Settings(_env_file=None, TREND_PROVIDER="nope")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        get_trend_provider(settings)
