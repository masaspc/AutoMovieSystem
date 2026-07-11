"""決定的なFake TTSプロバイダー(テスト・APIキー未設定環境向け: D-006)。

`wave` 標準モジュールのみでWAVを生成する(実TTS APIは一切呼ばない)。
同一テキストからは常に同一の音声データ(＝同一checksum)を生成する。
"""

from __future__ import annotations

import hashlib
import io
import math
import struct
import wave
from pathlib import Path

from app.providers.tts.base import TTSResult

SAMPLE_RATE = 16_000
_BASE_FREQUENCY_HZ = 440.0
_SECONDS_PER_CHAR = 0.15
_MIN_DURATION_SECONDS = 1.0
_CHUNK_SECONDS = 0.2
_AMPLITUDE = 12000


def _duration_for_text(text: str, speed_scale: float = 1.0) -> float:
    return max(_MIN_DURATION_SECONDS, len(text) * _SECONDS_PER_CHAR / speed_scale)


def _generate_pcm_samples(text: str, speed_scale: float = 1.0) -> list[int]:
    """テキストのSHA256をシードに、正弦波+無音を交互配置したPCMサンプル列を生成する。"""
    seed = hashlib.sha256(text.encode("utf-8")).digest()
    duration_seconds = _duration_for_text(text, speed_scale)
    total_samples = int(duration_seconds * SAMPLE_RATE)
    chunk_samples = max(1, int(_CHUNK_SECONDS * SAMPLE_RATE))

    samples: list[int] = []
    chunk_index = 0
    while len(samples) < total_samples:
        seed_byte = seed[chunk_index % len(seed)]
        is_tone_chunk = (seed_byte % 2) == 0
        # シードバイトからチャンクごとに周波数をわずかに変化させる(±100Hz程度)。
        frequency = _BASE_FREQUENCY_HZ + (seed_byte % 100)
        for i in range(chunk_samples):
            if len(samples) >= total_samples:
                break
            if is_tone_chunk:
                t = i / SAMPLE_RATE
                value = int(_AMPLITUDE * math.sin(2 * math.pi * frequency * t))
            else:
                value = 0
            samples.append(value)
        chunk_index += 1

    return samples


def synthesize_wav_bytes(text: str, speed_scale: float = 1.0) -> bytes:
    """テキストから決定的なWAVバイト列を生成する(16kHz mono 16bit)。"""
    samples = _generate_pcm_samples(text, speed_scale)
    frames = struct.pack(f"<{len(samples)}h", *samples)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(frames)
    return buffer.getvalue()


class FakeTTSProvider:
    """決定的なFake実装。同一テキストは常に同一checksumのWAVを生成する。"""

    async def synthesize(
        self,
        *,
        text: str,
        voice: str,
        output_path: Path,
        idempotency_key: str,
        speed_scale: float = 1.0,
    ) -> TTSResult:
        wav_bytes = synthesize_wav_bytes(text, speed_scale)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(wav_bytes)

        checksum = hashlib.sha256(wav_bytes).hexdigest()
        duration_seconds = _duration_for_text(text, speed_scale)

        return TTSResult(
            output_path=output_path,
            duration_seconds=duration_seconds,
            sample_rate=SAMPLE_RATE,
            checksum=checksum,
        )
