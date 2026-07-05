"""TTSプロバイダーの選択(設定 `TTS_PROVIDER=fake|generic_command`: D-006)。"""

from __future__ import annotations

import json

from app.core.config import Settings, get_settings
from app.providers.tts.base import TTSProvider
from app.providers.tts.fake import FakeTTSProvider
from app.providers.tts.generic_command import GenericCommandTTSProvider


def get_tts_provider(settings: Settings | None = None) -> TTSProvider:
    """設定に基づきTTSプロバイダーのインスタンスを返す。"""
    settings = settings or get_settings()
    if settings.TTS_PROVIDER == "fake":
        return FakeTTSProvider()
    if settings.TTS_PROVIDER == "generic_command":
        if not settings.TTS_GENERIC_COMMAND_TEMPLATE:
            raise ValueError(
                "TTS_PROVIDER=generic_command には TTS_GENERIC_COMMAND_TEMPLATE の設定が必要です"
            )
        template = json.loads(settings.TTS_GENERIC_COMMAND_TEMPLATE)
        if not isinstance(template, list) or not all(isinstance(x, str) for x in template):
            raise ValueError("TTS_GENERIC_COMMAND_TEMPLATE は文字列配列のJSONである必要があります")
        return GenericCommandTTSProvider(
            template, timeout_seconds=settings.TTS_GENERIC_COMMAND_TIMEOUT_SECONDS
        )
    raise ValueError(f"Unknown TTS_PROVIDER: {settings.TTS_PROVIDER!r}")
