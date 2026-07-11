"""VOICEVOX Engine HTTP API用のTTSプロバイダー。"""

from __future__ import annotations

import hashlib
import wave
from pathlib import Path

import httpx

from app.core.config import Settings
from app.providers.tts.base import TTSProviderError, TTSResult


class VoicevoxTTSProvider:
    """`/audio_query` と `/synthesis` を使い、話者別のWAVを生成する。"""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.VOICEVOX_BASE_URL.rstrip("/")
        self._timeout_seconds = settings.VOICEVOX_TIMEOUT_SECONDS
        self._speaker_ids = {
            "zundamon": settings.VOICEVOX_SPEAKER_ZUNDAMON,
            "metan": settings.VOICEVOX_SPEAKER_METAN,
            "tsumugi": settings.VOICEVOX_SPEAKER_TSUMUGI,
        }

    def _speaker_id(self, voice: str) -> int:
        try:
            return self._speaker_ids[voice]
        except KeyError as exc:
            raise TTSProviderError(
                f"VOICEVOXの話者が不明です: {voice!r} (zundamon/metan/tsumugiのみ対応)"
            ) from exc

    async def synthesize(
        self,
        *,
        text: str,
        voice: str,
        output_path: Path,
        idempotency_key: str,
        speed_scale: float = 1.0,
    ) -> TTSResult:
        del idempotency_key
        speaker = self._speaker_id(voice)
        timeout = httpx.Timeout(self._timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                query_response = await client.post(
                    f"{self._base_url}/audio_query",
                    params={"text": text, "speaker": speaker},
                )
                query_response.raise_for_status()
                audio_query = query_response.json()
                audio_query["speedScale"] = speed_scale
                synthesis_response = await client.post(
                    f"{self._base_url}/synthesis",
                    params={"speaker": speaker},
                    json=audio_query,
                )
                synthesis_response.raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            raise TTSProviderError(f"VOICEVOX音声合成に失敗しました: {exc}") from exc

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(synthesis_response.content)
        try:
            with wave.open(str(output_path), "rb") as wav_file:
                sample_rate = wav_file.getframerate()
                n_frames = wav_file.getnframes()
                duration_seconds = (n_frames / sample_rate) if sample_rate else 0.0
        except (wave.Error, EOFError) as exc:
            raise TTSProviderError(f"VOICEVOX出力がWAVとして読めません: {output_path}") from exc

        return TTSResult(
            output_path=output_path,
            duration_seconds=duration_seconds,
            sample_rate=sample_rate,
            checksum=hashlib.sha256(synthesis_response.content).hexdigest(),
        )
