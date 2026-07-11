"""FFmpeg/ffprobe の可用性チェック(Phase 1: 運用安定化)。

D-007 のパス解決(`Settings.resolved_ffmpeg_path` / `resolved_ffprobe_path`)は
見つからない場合にバイナリ名をそのまま返す設計のため、「解決されたパスが実際に
実行可能か」は別途この関数で判定する。レンダリング開始後の FileNotFoundError で
初めて発覚するのを防ぎ、起動時ログ・/health・管理画面の警告すべてで同じ判定を使う。

判定は2段階:
- 既定(存在チェックのみ): /health・ダッシュボードなどリクエストごとに呼ばれる箇所用。
  subprocess を起動しないため軽量。
- `verify_execution=True`(`-version` 実行): 起動時チェック用。実際に実行可能である
  ことまで確認する(壊れたバイナリ・アーキテクチャ不一致等も検出できる)。
"""

from __future__ import annotations

import shutil
import subprocess  # noqa: S404 引数配列のみで使用。shell=Trueは使わない。
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings, get_settings

_VERSION_CHECK_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class MediaToolStatus:
    """FFmpeg/ffprobe の解決済みパスと実行可能性。"""

    ffmpeg_path: str
    ffmpeg_available: bool
    ffprobe_path: str
    ffprobe_available: bool

    @property
    def ok(self) -> bool:
        return self.ffmpeg_available and self.ffprobe_available


def _binary_exists(resolved_path: str) -> bool:
    """解決済みパス(絶対パスまたはバイナリ名)が実行可能ファイルを指すか判定する。"""
    if shutil.which(resolved_path) is not None:
        return True
    path = Path(resolved_path)
    return path.is_absolute() and path.is_file()


def _binary_runs(resolved_path: str) -> bool:
    """`<binary> -version` が実際に実行できるか確認する(起動時チェック用)。"""
    if not _binary_exists(resolved_path):
        return False
    try:
        completed = subprocess.run(  # noqa: S603 - 引数配列固定。shell=False
            [resolved_path, "-version"],
            capture_output=True,
            timeout=_VERSION_CHECK_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def check_media_tools(
    settings: Settings | None = None, *, verify_execution: bool = False
) -> MediaToolStatus:
    """FFmpeg/ffprobe の可用性を判定する。

    Args:
        settings: 設定(未指定なら `get_settings()`)。
        verify_execution: Trueなら `-version` の実行成功まで確認する(起動時チェック用。
            リクエストごとに呼ぶ箇所では既定のFalse=存在チェックのみを使うこと)。
    """
    settings = settings or get_settings()
    ffmpeg_path = settings.resolved_ffmpeg_path
    ffprobe_path = settings.resolved_ffprobe_path
    checker = _binary_runs if verify_execution else _binary_exists
    return MediaToolStatus(
        ffmpeg_path=ffmpeg_path,
        ffmpeg_available=checker(ffmpeg_path),
        ffprobe_path=ffprobe_path,
        ffprobe_available=checker(ffprobe_path),
    )
