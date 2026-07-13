"""トレンド情報源の共通型・Protocol(D-026)。

外部記事からは見出し・リンク・短い要約のみを扱う(本文の取得・転載は行わない。
docs/content-policy.md準拠)。実装はこのパッケージ配下のみ(fake.py / rss.py)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

TREND_TITLE_MAX_CHARS = 255
TREND_SOURCE_MAX_CHARS = 255
TREND_SUMMARY_MAX_CHARS = 200
TREND_URL_MAX_CHARS = 2048


def truncate_trend_text(value: str, *, max_chars: int) -> str:
    """前後空白を除き、省略記号込みで指定文字数以内へ収める。"""
    normalized = value.strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1] + "…"


def normalize_trend_url(value: str) -> str:
    """記事・フィードURLを安全なHTTP(S) URLへ正規化する。"""
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > TREND_URL_MAX_CHARS
        or "\\" in normalized
        or any(character.isspace() or ord(character) < 0x20 for character in normalized)
    ):
        raise ValueError("トレンドURLには2048文字以内のhttp(s) URLを指定してください")
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("トレンドURLにはhttp(s) URLを指定してください")
    return normalized


@dataclass(frozen=True)
class TrendItem:
    """トレンド記事1件(見出し+リンク+要約のみ)。"""

    title: str
    url: str
    source: str
    summary: str
    published_at: datetime | None


class TrendProviderError(Exception):
    """トレンドプロバイダー共通エラー基底。"""


@runtime_checkable
class TrendProvider(Protocol):
    """トレンド情報源の共通インターフェース。"""

    async def fetch_latest(self, *, limit: int) -> list[TrendItem]: ...
