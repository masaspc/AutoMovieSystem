"""汎用コマンドTTSプロバイダー(設定のコマンドテンプレートを引数配列で実行)。

コマンドテンプレート例:
`["voicevox_cli", "--text", "{text}", "--voice", "{voice}", "--out", "{output}"]`。
`{text}`/`{voice}`/`{output}` は配列要素単位で置換する(文字列連結・shell実行は行わない)。
"""

from __future__ import annotations

import hashlib
import wave
from pathlib import Path

from app.core.subprocess_util import SubprocessError, run_checked
from app.providers.tts.base import TTSProviderError, TTSResult

_DEFAULT_TIMEOUT_SECONDS = 60.0


def substitute_placeholders(
    template: list[str], *, text: str, voice: str, output: str
) -> list[str]:
    """コマンドテンプレートの各要素内で `{text}`/`{voice}`/`{output}` を置換する。

    文字列連結でコマンド全体を組み立てるのではなく、配列要素ごとに置換するため
    シェルインジェクションの余地がない。
    """
    substituted: list[str] = []
    for arg in template:
        replaced = arg.replace("{text}", text).replace("{voice}", voice).replace("{output}", output)
        substituted.append(replaced)
    return substituted


class GenericCommandTTSProvider:
    """設定のコマンドテンプレートを `shell=False` で実行するTTSプロバイダー。"""

    def __init__(
        self, command_template: list[str], *, timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        if not command_template:
            raise ValueError("command_templateは空にできません")
        self._command_template = command_template
        self._timeout_seconds = timeout_seconds

    async def synthesize(
        self,
        *,
        text: str,
        voice: str,
        output_path: Path,
        idempotency_key: str,
    ) -> TTSResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        args = substitute_placeholders(
            self._command_template, text=text, voice=voice, output=str(output_path)
        )

        try:
            run_checked(args, timeout=self._timeout_seconds)
        except SubprocessError as exc:
            raise TTSProviderError(f"TTSコマンドの実行に失敗しました: {exc}") from exc

        if not output_path.exists():
            raise TTSProviderError(f"TTSコマンドが出力ファイルを生成しませんでした: {output_path}")

        try:
            with wave.open(str(output_path), "rb") as wav_file:
                sample_rate = wav_file.getframerate()
                n_frames = wav_file.getnframes()
                duration_seconds = (n_frames / sample_rate) if sample_rate else 0.0
        except (wave.Error, EOFError) as exc:
            raise TTSProviderError(f"TTS出力がWAVとして読めません: {output_path}") from exc

        checksum = hashlib.sha256(output_path.read_bytes()).hexdigest()

        return TTSResult(
            output_path=output_path,
            duration_seconds=duration_seconds,
            sample_rate=sample_rate,
            checksum=checksum,
        )
