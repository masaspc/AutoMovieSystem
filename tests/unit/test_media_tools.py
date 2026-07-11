"""FFmpeg/ffprobe 可用性チェック(app/services/media/tools.py)の単体テスト。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.media.tools import check_media_tools


def _settings(ffmpeg: str, ffprobe: str) -> Settings:
    return Settings(_env_file=None, FFMPEG_PATH=ffmpeg, FFPROBE_PATH=ffprobe)  # type: ignore[call-arg]


def test_missing_binaries_are_reported_unavailable(tmp_path: Path) -> None:
    status = check_media_tools(
        _settings(str(tmp_path / "no-such-ffmpeg.exe"), str(tmp_path / "no-such-ffprobe.exe"))
    )
    assert status.ffmpeg_available is False
    assert status.ffprobe_available is False
    assert status.ok is False


def test_existing_files_are_reported_available(tmp_path: Path) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    ffmpeg.write_bytes(b"stub")
    ffprobe.write_bytes(b"stub")

    status = check_media_tools(_settings(str(ffmpeg), str(ffprobe)))
    assert status.ffmpeg_available is True
    assert status.ffprobe_available is True
    assert status.ok is True


def test_bare_binary_name_not_on_path_is_unavailable() -> None:
    """D-007のフォールバック(未発見時にバイナリ名をそのまま返す)を実行可能と誤判定しない。"""
    status = check_media_tools(
        _settings("definitely-not-a-real-binary-name", "definitely-not-a-real-binary-name-2")
    )
    assert status.ok is False


def test_verify_execution_rejects_non_executable_stub(tmp_path: Path) -> None:
    """存在するだけで実行できないファイルは verify_execution=True で不可と判定する。"""
    stub = tmp_path / "ffmpeg.exe"
    stub.write_bytes(b"not a real executable")
    status = check_media_tools(
        _settings(str(stub), str(stub)),
        verify_execution=True,
    )
    assert status.ok is False


def test_verify_execution_accepts_working_command(tmp_path: Path) -> None:
    """`-version` が成功するコマンドは verify_execution=True で可と判定する。

    実FFmpegに依存しないよう、引数を無視して正常終了するスタブ実行ファイルを
    プラットフォームごとに作成して代用する(実行確認ロジック自体の検証が目的)。
    """
    if sys.platform == "win32":
        stub = tmp_path / "ffmpeg.bat"
        stub.write_text("@exit /b 0\n", encoding="ascii")
    else:
        stub = tmp_path / "ffmpeg"
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        stub.chmod(0o755)

    status = check_media_tools(_settings(str(stub), str(stub)), verify_execution=True)
    assert status.ok is True


@pytest.mark.parametrize("field", ["ffmpeg", "ffprobe"])
def test_health_endpoint_reports_media_tools(client, field) -> None:  # type: ignore[no-untyped-def]
    response = client.get("/health")
    body = response.json()
    assert response.status_code == 200
    assert body[field] in ("ok", "missing")
