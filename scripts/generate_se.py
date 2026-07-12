"""効果音(SE)を自前合成する(権利リスクゼロのオリジナル音源)。

`uv run python scripts/generate_se.py` で assets/se/ にWAVを生成する。
生成音はこのリポジトリのオリジナル(合成波形)であり、クレジット表記不要・商用利用可。

- transition.wav: セクション切替のスウィープ音(ノイズ+ハイパスの短いシュッ)
- quiz.wav: クイズ出題のベル(2音チャイム)
- point.wav: 要点強調のポップ音
"""

from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 48_000
OUT_DIR = Path(__file__).resolve().parent.parent / "assets" / "se"


def _write_wav(path: Path, samples: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = [max(-1.0, min(1.0, s)) for s in samples]
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(
            struct.pack(f"<{len(clipped)}h", *(int(s * 32767) for s in clipped))
        )


def _envelope(index: int, total: int, *, attack: float, release: float) -> float:
    t = index / SAMPLE_RATE
    duration = total / SAMPLE_RATE
    if t < attack:
        return t / attack
    if t > duration - release:
        return max(0.0, (duration - t) / release)
    return 1.0


def transition_sweep(duration: float = 0.45) -> list[float]:
    """ホワイトノイズの周波数スウィープ(シュッ)。決定的シードで再現可能。"""
    rng = random.Random("se-transition")  # noqa: S311 - 暗号用途ではない(音色の決定的生成)
    n = int(SAMPLE_RATE * duration)
    samples: list[float] = []
    prev = 0.0
    for i in range(n):
        progress = i / n
        # ローパス係数を時間とともに開く(こもった音→抜ける音)。
        alpha = 0.02 + 0.55 * progress
        noise = rng.uniform(-1.0, 1.0)
        prev = prev + alpha * (noise - prev)
        env = _envelope(i, n, attack=0.05, release=0.25)
        samples.append(prev * env * 0.5)
    return samples


def quiz_chime(duration: float = 0.9) -> list[float]:
    """2音チャイム(E5->A5)。出題の合図。"""
    n = int(SAMPLE_RATE * duration)
    samples = []
    for i in range(n):
        t = i / SAMPLE_RATE
        freq = 659.25 if t < 0.25 else 880.0
        tone = math.sin(2 * math.pi * freq * t) + 0.35 * math.sin(2 * math.pi * freq * 2 * t)
        env = _envelope(i, n, attack=0.01, release=0.5)
        samples.append(tone * env * 0.35)
    return samples


def point_pop(duration: float = 0.25) -> list[float]:
    """要点強調のポップ音(短い上昇トーン)。"""
    n = int(SAMPLE_RATE * duration)
    samples = []
    for i in range(n):
        t = i / SAMPLE_RATE
        freq = 520.0 + 700.0 * (t / duration)
        tone = math.sin(2 * math.pi * freq * t)
        env = _envelope(i, n, attack=0.005, release=0.12)
        samples.append(tone * env * 0.4)
    return samples


def main() -> None:
    _write_wav(OUT_DIR / "transition.wav", transition_sweep())
    _write_wav(OUT_DIR / "quiz.wav", quiz_chime())
    _write_wav(OUT_DIR / "point.wav", point_pop())
    print(f"generated SE files in {OUT_DIR}")


if __name__ == "__main__":
    main()
