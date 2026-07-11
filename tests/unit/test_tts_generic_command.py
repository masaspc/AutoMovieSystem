from __future__ import annotations

import asyncio
import sys
import wave
from pathlib import Path

import pytest

from app.providers.tts.base import TTSProviderError
from app.providers.tts.generic_command import (
    GenericCommandTTSProvider,
    substitute_placeholders,
)

# {text}/{voice}/{output} を受け取り、テキスト長に応じた長さのWAVを書き出すスタブ。
# 文字列連結・shell実行を経由しない(引数配列そのままpythonへ渡される)ことを検証する。
_STUB_SCRIPT = """
import struct
import sys
import wave

text = sys.argv[1]
voice = sys.argv[2]
output = sys.argv[3]

with open(output.replace(".wav", ".args.txt"), "w", encoding="utf-8") as f:
    f.write(text + "|" + voice)

n_samples = max(1, len(text)) * 100
frames = struct.pack(f"<{n_samples}h", *([0] * n_samples))
with wave.open(output, "wb") as wav_file:
    wav_file.setnchannels(1)
    wav_file.setsampwidth(2)
    wav_file.setframerate(16000)
    wav_file.writeframes(frames)
"""


@pytest.fixture
def stub_script(tmp_path: Path) -> Path:
    script_path = tmp_path / "tts_stub.py"
    script_path.write_text(_STUB_SCRIPT, encoding="utf-8")
    return script_path


def test_substitute_placeholders_replaces_each_element_independently() -> None:
    template = [
        "cmd",
        "--text",
        "{text}",
        "--voice",
        "{voice}",
        "--out",
        "{output}",
        "--speed",
        "{speed}",
    ]
    result = substitute_placeholders(
        template, text="hello world", voice="v1", output="out/audio.wav", speed=1.25
    )

    assert result == [
        "cmd",
        "--text",
        "hello world",
        "--voice",
        "v1",
        "--out",
        "out/audio.wav",
        "--speed",
        "1.25",
    ]
    # プレースホルダーを含まない要素は変化しない。
    assert result[0] == "cmd"


def test_substitute_placeholders_does_not_use_shell_string_concatenation() -> None:
    """textに `;` や `&&` を含んでいても、単一の配列要素として扱われる(シェル解釈されない)。"""
    template = ["echo", "{text}"]
    dangerous_text = "hello; rm -rf / && echo pwned"
    result = substitute_placeholders(template, text=dangerous_text, voice="v", output="o")
    assert result == ["echo", dangerous_text]
    assert len(result) == 2  # 危険な文字列が複数の引数に分割されていない


def test_generic_command_tts_provider_runs_with_substituted_args(
    tmp_path: Path, stub_script: Path
) -> None:
    output_path = tmp_path / "generated.wav"
    template = [sys.executable, str(stub_script), "{text}", "{voice}", "{output}"]
    provider = GenericCommandTTSProvider(template, timeout_seconds=30.0)

    result = asyncio.run(
        provider.synthesize(
            text="こんにちは",
            voice="voice-x",
            output_path=output_path,
            idempotency_key="key-1",
        )
    )

    assert output_path.exists()
    with wave.open(str(output_path), "rb") as wav_file:
        assert wav_file.getframerate() == 16000
    assert result.sample_rate == 16000
    assert result.duration_seconds > 0
    assert result.checksum == __import__("hashlib").sha256(output_path.read_bytes()).hexdigest()

    args_file = tmp_path / "generated.args.txt"
    assert args_file.read_text(encoding="utf-8") == "こんにちは|voice-x"


def test_generic_command_tts_provider_raises_when_output_missing(tmp_path: Path) -> None:
    # 出力ファイルを作らないコマンド(true相当)。
    template = [sys.executable, "-c", "pass"]
    provider = GenericCommandTTSProvider(template, timeout_seconds=10.0)

    with pytest.raises(TTSProviderError, match="出力ファイルを生成しませんでした"):
        asyncio.run(
            provider.synthesize(
                text="x",
                voice="v",
                output_path=tmp_path / "missing.wav",
                idempotency_key="key-2",
            )
        )


def test_generic_command_tts_provider_rejects_empty_template() -> None:
    with pytest.raises(ValueError, match="空にできません"):
        GenericCommandTTSProvider([])
