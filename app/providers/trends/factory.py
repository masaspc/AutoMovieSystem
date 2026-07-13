"""TrendProviderの選択(設定 `TREND_PROVIDER=fake|rss`)。"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.providers.trends.base import TrendProvider
from app.providers.trends.fake import FakeTrendProvider


def get_trend_provider(settings: Settings | None = None) -> TrendProvider:
    settings = settings or get_settings()
    if settings.TREND_PROVIDER == "fake":
        return FakeTrendProvider()
    if settings.TREND_PROVIDER == "rss":
        from app.providers.trends.rss import RSSTrendProvider

        return RSSTrendProvider(settings)
    raise ValueError(f"未知のTREND_PROVIDERです: {settings.TREND_PROVIDER!r} (fake|rss)")
