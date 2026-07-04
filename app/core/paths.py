"""生成物パスの境界検証(D-008)。

`generated/` 配下であることを必須にし、ディレクトリトラバーサルを拒否する。
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings


def resolve_generated_path(relative: str | Path) -> Path:
    """`GENERATED_DIR` 配下の絶対パスを返す。境界外・トラバーサルは ValueError。

    Args:
        relative: `GENERATED_DIR` からの相対パス(文字列 or Path)。

    Raises:
        ValueError: 絶対パスが渡された場合、または解決後のパスが
            `GENERATED_DIR` の外側になる場合(`..` によるトラバーサル等)。
    """
    settings = get_settings()
    base_dir = Path(settings.GENERATED_DIR).resolve()

    relative_path = Path(relative)
    if relative_path.is_absolute():
        raise ValueError(f"絶対パスは許可されません: {relative}")

    candidate = (base_dir / relative_path).resolve()

    try:
        candidate.relative_to(base_dir)
    except ValueError as exc:
        raise ValueError(
            f"生成物パスが GENERATED_DIR の外側です: {relative} -> {candidate}"
        ) from exc

    return candidate
