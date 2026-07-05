"""YouTubeプロバイダーの選択(設定 `YOUTUBE_PROVIDER=fake|real`: D-006)。"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.providers.youtube.base import YouTubeProvider
from app.providers.youtube.fake import FakeYouTubeProvider


def get_youtube_provider(settings: Settings | None = None) -> YouTubeProvider:
    """設定に基づきYouTubeプロバイダーのインスタンスを返す。"""
    settings = settings or get_settings()
    if settings.YOUTUBE_PROVIDER == "fake":
        return FakeYouTubeProvider()
    if settings.YOUTUBE_PROVIDER == "real":
        from app.providers.youtube.real import RealYouTubeProvider

        return RealYouTubeProvider(settings)
    raise ValueError(f"Unknown YOUTUBE_PROVIDER: {settings.YOUTUBE_PROVIDER!r}")
