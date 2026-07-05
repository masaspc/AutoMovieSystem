"""subprocess共通基盤(docs/architecture.md セキュリティ要点)。

FFmpeg/ffprobe/TTSコマンド実行はすべてこのモジュール経由で行う。
`shell=True` は絶対に使わない。引数は必ずリストで渡す。
"""

from __future__ import annotations

import subprocess  # noqa: S404 - shell=False固定でこのモジュール内にのみ隔離する
from dataclasses import dataclass

_MAX_STDERR_SUMMARY_LENGTH = 2000


@dataclass(frozen=True)
class CompletedProcessResult:
    """`run_checked` の戻り値。"""

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class SubprocessError(RuntimeError):
    """外部コマンドの実行失敗(非ゼロ終了 or タイムアウト)。シークレットは含めない。"""


class SubprocessTimeoutError(SubprocessError):
    """外部コマンドがタイムアウトした場合。"""


def run_checked(
    args: list[str],
    *,
    timeout: float,
    check: bool = True,
) -> CompletedProcessResult:
    """外部コマンドを `shell=False` で実行する。

    Args:
        args: コマンドと引数のリスト(文字列連結禁止。要素単位でそのままexecvpに渡る)。
        timeout: 秒単位のタイムアウト。
        check: True(既定)の場合、非ゼロ終了コードで `SubprocessError` を送出する。

    Raises:
        SubprocessTimeoutError: タイムアウトした場合。
        SubprocessError: `check=True` かつ非ゼロ終了の場合。
    """
    if not args:
        raise ValueError("argsは空にできません")
    if not all(isinstance(a, str) for a in args):
        raise TypeError("argsの全要素はstrである必要があります(文字列連結禁止)")

    try:
        completed = subprocess.run(  # noqa: S603 - argsはリスト固定、shell=False
            args,
            shell=False,
            capture_output=True,
            # Windowsのロケール既定コードページ(例: cp932)に依存すると、FFmpeg/ffprobeの
            # UTF-8出力(日本語パス等を含むJSON)が文字化けし、JSONパース等が壊れる。
            # 常にUTF-8で明示的にデコードする(不正バイトは置換文字に変換して継続する)。
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stderr_text: str = (
            exc.stderr.decode("utf-8", errors="replace")
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or "")
        )
        stderr_tail = stderr_text[-_MAX_STDERR_SUMMARY_LENGTH:]
        raise SubprocessTimeoutError(
            f"コマンドがタイムアウトしました(timeout={timeout}s): "
            f"{args[0]} ... stderr={stderr_tail}"
        ) from exc

    result = CompletedProcessResult(
        args=tuple(args),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )

    if check and completed.returncode != 0:
        stderr_tail = (completed.stderr or "")[-_MAX_STDERR_SUMMARY_LENGTH:]
        raise SubprocessError(
            f"コマンドが失敗しました(returncode={completed.returncode}): "
            f"{args[0]} ... stderr={stderr_tail}"
        )

    return result
