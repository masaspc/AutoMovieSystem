from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.config import Settings
from app.providers.tts.base import TTSProviderError
from app.providers.tts.fake import synthesize_wav_bytes
from app.providers.tts.voicevox import VoicevoxTTSProvider


def _response(
    status_code: int, *, json: dict | None = None, content: bytes = b""
) -> httpx.Response:
    kwargs: dict[str, object] = {"request": httpx.Request("POST", "http://voicevox.test")}
    if json is not None:
        kwargs["json"] = json
    else:
        kwargs["content"] = content
    return httpx.Response(status_code, **kwargs)


def test_voicevox_synthesizes_selected_character_to_wav(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, VOICEVOX_BASE_URL="http://voicevox.test")  # type: ignore[call-arg]
    provider = VoicevoxTTSProvider(settings)
    mock_post = AsyncMock(
        side_effect=[
            _response(200, json={"speedScale": 1.0}),
            _response(200, content=synthesize_wav_bytes("こんにちは")),
        ]
    )

    async def run() -> object:
        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            return await provider.synthesize(
                text="こんにちは",
                voice="metan",
                output_path=tmp_path / "metan.wav",
                idempotency_key="test",
            )

    result = asyncio.run(run())
    assert result.output_path.exists()  # type: ignore[attr-defined]
    assert result.duration_seconds > 0  # type: ignore[attr-defined]
    assert mock_post.await_args_list[0].kwargs["params"]["speaker"] == 2
    assert mock_post.await_args_list[1].kwargs["params"]["speaker"] == 2


def test_voicevox_rejects_unknown_character(tmp_path: Path) -> None:
    provider = VoicevoxTTSProvider(Settings(_env_file=None))  # type: ignore[call-arg]

    with pytest.raises(TTSProviderError, match="unknown"):
        asyncio.run(
            provider.synthesize(
                text="test",
                voice="unknown",
                output_path=tmp_path / "unknown.wav",
                idempotency_key="test",
            )
        )
