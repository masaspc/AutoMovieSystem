from __future__ import annotations

import asyncio
import wave
from pathlib import Path

from app.providers.tts.base import TTSResult
from app.providers.tts.fake import SAMPLE_RATE, FakeTTSProvider


def test_fake_tts_is_deterministic_for_same_text(tmp_path: Path) -> None:
    provider = FakeTTSProvider()

    result1: TTSResult = asyncio.run(
        provider.synthesize(
            text="こんにちは、世界",
            voice="voice-a",
            output_path=tmp_path / "out1.wav",
            idempotency_key="key-1",
        )
    )
    result2: TTSResult = asyncio.run(
        provider.synthesize(
            text="こんにちは、世界",
            voice="voice-a",
            output_path=tmp_path / "out2.wav",
            idempotency_key="key-2",
        )
    )

    assert result1.checksum == result2.checksum
    assert (tmp_path / "out1.wav").read_bytes() == (tmp_path / "out2.wav").read_bytes()


def test_fake_tts_different_text_gives_different_checksum(tmp_path: Path) -> None:
    provider = FakeTTSProvider()

    result1 = asyncio.run(
        provider.synthesize(
            text="テキストA",
            voice="voice-a",
            output_path=tmp_path / "a.wav",
            idempotency_key="key-a",
        )
    )
    result2 = asyncio.run(
        provider.synthesize(
            text="テキストBBB",
            voice="voice-a",
            output_path=tmp_path / "b.wav",
            idempotency_key="key-b",
        )
    )

    assert result1.checksum != result2.checksum


def test_fake_tts_duration_scales_with_text_length(tmp_path: Path) -> None:
    provider = FakeTTSProvider()

    short_result = asyncio.run(
        provider.synthesize(
            text="短い",
            voice="voice-a",
            output_path=tmp_path / "short.wav",
            idempotency_key="key-short",
        )
    )
    long_result = asyncio.run(
        provider.synthesize(
            text="これはとても長いテキストです" * 5,
            voice="voice-a",
            output_path=tmp_path / "long.wav",
            idempotency_key="key-long",
        )
    )

    assert short_result.duration_seconds >= 1.0  # 最低1秒
    assert long_result.duration_seconds > short_result.duration_seconds


def test_fake_tts_speed_scale_shortens_duration(tmp_path: Path) -> None:
    provider = FakeTTSProvider()
    text = "十分に長いテスト文章です" * 3
    normal = asyncio.run(
        provider.synthesize(
            text=text,
            voice="default",
            output_path=tmp_path / "normal.wav",
            idempotency_key="normal",
        )
    )
    fast = asyncio.run(
        provider.synthesize(
            text=text,
            voice="default",
            output_path=tmp_path / "fast.wav",
            idempotency_key="fast",
            speed_scale=1.5,
        )
    )
    assert fast.duration_seconds < normal.duration_seconds


def test_fake_tts_produces_valid_16khz_mono_wav(tmp_path: Path) -> None:
    provider = FakeTTSProvider()
    output_path = tmp_path / "out.wav"

    result = asyncio.run(
        provider.synthesize(
            text="テスト音声",
            voice="voice-a",
            output_path=output_path,
            idempotency_key="key-1",
        )
    )

    assert result.sample_rate == SAMPLE_RATE
    with wave.open(str(output_path), "rb") as wav_file:
        assert wav_file.getframerate() == SAMPLE_RATE
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2  # 16bit
