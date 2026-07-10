"""時刻ユーティリティ。

DBには従来どおり naive な UTC datetime を保存する(既存レコード・比較ロジックとの
互換維持)。`datetime.utcnow()` は Python 3.12 で非推奨・将来削除のため、
aware な `datetime.now(UTC)` から tzinfo を落とす本ヘルパーに一本化する。
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow_naive() -> datetime:
    """現在のUTC時刻を naive datetime で返す(DB保存・比較用の標準)。"""
    return datetime.now(UTC).replace(tzinfo=None)
