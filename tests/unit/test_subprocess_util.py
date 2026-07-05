from __future__ import annotations

import sys

import pytest

from app.core.subprocess_util import (
    SubprocessError,
    SubprocessTimeoutError,
    run_checked,
)


def test_run_checked_returns_stdout_on_success() -> None:
    result = run_checked([sys.executable, "-c", "print('hello')"], timeout=10.0)
    assert result.returncode == 0
    assert "hello" in result.stdout


def test_run_checked_raises_on_nonzero_exit() -> None:
    with pytest.raises(SubprocessError):
        run_checked([sys.executable, "-c", "import sys; sys.exit(3)"], timeout=10.0)


def test_run_checked_does_not_raise_when_check_false() -> None:
    result = run_checked(
        [sys.executable, "-c", "import sys; sys.exit(3)"], timeout=10.0, check=False
    )
    assert result.returncode == 3


def test_run_checked_raises_on_timeout() -> None:
    with pytest.raises(SubprocessTimeoutError):
        run_checked(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout=0.2,
        )


def test_run_checked_rejects_empty_args() -> None:
    with pytest.raises(ValueError, match="空にできません"):
        run_checked([], timeout=10.0)


def test_run_checked_rejects_non_string_args() -> None:
    with pytest.raises(TypeError, match="str"):
        run_checked([sys.executable, 123], timeout=10.0)  # type: ignore[list-item]


def test_run_checked_does_not_use_shell() -> None:
    """引数中の特殊文字(;や&&)がシェル解釈されず、そのまま単一引数として渡ること。"""
    dangerous_arg = "hello; echo pwned"
    result = run_checked(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", dangerous_arg],
        timeout=10.0,
    )
    assert result.stdout.strip() == dangerous_arg
    assert "pwned" not in result.stdout.replace(dangerous_arg, "")
