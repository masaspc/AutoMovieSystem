"""実ffmpegを使った無音/音量検査の統合テスト(@pytest.mark.media)。

無音WAV(silencedetect)+ビープ音WAV(volumedetect)を生成し、
`app.services.reviews.machine` のffmpeg呼び出しが正しくstderrを解釈することを検証する。
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest

from app.services.reviews import machine

# FFmpeg未検出時のskip/失敗判定は conftest.py の ffmpeg_required fixture に一本化する。
pytestmark = [pytest.mark.media, pytest.mark.usefixtures("ffmpeg_required")]

_SAMPLE_RATE = 48_000


def _write_silence_wav(path: Path, *, duration_seconds: float) -> None:
    n_frames = int(_SAMPLE_RATE * duration_seconds)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(_SAMPLE_RATE)
        wav_file.writeframes(struct.pack(f"<{n_frames}h", *([0] * n_frames)))


def _write_beep_wav(path: Path, *, duration_seconds: float, frequency_hz: float = 440.0) -> None:
    n_frames = int(_SAMPLE_RATE * duration_seconds)
    amplitude = 20000
    samples = [
        int(amplitude * math.sin(2 * math.pi * frequency_hz * (i / _SAMPLE_RATE)))
        for i in range(n_frames)
    ]
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(_SAMPLE_RATE)
        wav_file.writeframes(struct.pack(f"<{n_frames}h", *samples))


def test_detect_silence_durations_finds_long_silence(tmp_path: Path) -> None:
    silent_path = tmp_path / "silence.wav"
    _write_silence_wav(silent_path, duration_seconds=12.0)

    durations = machine.detect_silence_durations(silent_path)

    assert durations, "無音区間が検出されるはずです"
    assert max(durations) >= 10.0


def test_detect_mean_volume_db_reports_audible_level_for_beep(tmp_path: Path) -> None:
    beep_path = tmp_path / "beep.wav"
    _write_beep_wav(beep_path, duration_seconds=3.0)

    mean_volume = machine.detect_mean_volume_db(beep_path)

    assert mean_volume is not None
    # 十分な振幅のビープ音であれば無音に近い極端な低音量にはならない。
    assert -20.0 <= mean_volume <= 0.0


def test_detect_silence_durations_reports_no_silence_for_beep(tmp_path: Path) -> None:
    beep_path = tmp_path / "beep_full.wav"
    _write_beep_wav(beep_path, duration_seconds=12.0)

    durations = machine.detect_silence_durations(beep_path)

    assert durations == []
