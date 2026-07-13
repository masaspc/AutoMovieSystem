"""トレンド情報源の共通型・Protocol(D-022)。

外部記事からは見出し・リンク・短い要約のみを扱う(本文の取得・転載は行わない。
docs/content-policy.md準拠)。実装はこのパッケージ配下のみ(fake.py / rss.py)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


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
