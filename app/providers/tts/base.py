"""TTSプロバイダー共通の型・Protocol(仕様§11)。

実装は `app/providers/tts/` 配下のみ(fake.py / generic_command.py)。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class TTSResult:
    """`TTSProvider.synthesize` の戻り値。"""

    output_path: Path
    duration_seconds: float
    sample_rate: int
    checksum: str


class TTSProviderError(Exception):
    """TTSプロバイダー共通エラー基底。"""


@runtime_checkable
class TTSProvider(Protocol):
    """TTSプロバイダー共通インターフェース(仕様§11)。"""

    async def synthesize(
        self,
        *,
        text: str,
        voice: str,
        output_path: Path,
        idempotency_key: str,
        speed_scale: float = 1.0,
        emotion: str = "neutral",
    ) -> TTSResult: ...
