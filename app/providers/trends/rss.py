"""RSS2.0/Atomフィードのトレンドプロバイダー。

取得するのは title / link / description(HTMLタグ除去・200字) / pubDate のみ。
本文は取得しない(docs/content-policy.md)。フィード単位でfail-soft
(到達不可・不正XMLはログ警告してスキップし、取得できたフィード分だけ返す)。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # noqa: S405 - 型・Element走査にのみ使用(解析はdefusedxml)
from datetime import datetime
from email.utils import parsedate_to_datetime

import httpx
from defusedxml import ElementTree as SafeET

from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.trends.base import TrendItem

logger = get_logger(__name__)

_SUMMARY_MAX_CHARS = 200
_FETCH_TIMEOUT_SECONDS = 15.0
_TAG_RE = re.compile(r"<[^>]+>")
_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _strip_html(text: str) -> str:
    return _TAG_RE.sub("", text).strip()


def _truncate(text: str) -> str:
    if len(text) <= _SUMMARY_MAX_CHARS:
        return text
    return text[:_SUMMARY_MAX_CHARS] + "…"


def _parse_rfc822(value: str) -> datetime | None:
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_rss2(root: ET.Element, source_hint: str) -> list[TrendItem]:
    channel = root.find("channel")
    if channel is None:
        return []
    source = (channel.findtext("title") or source_hint).strip()
    items: list[TrendItem] = []
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        if not title or not url:
            continue
        summary = _truncate(_strip_html(item.findtext("description") or ""))
        published = _parse_rfc822(item.findtext("pubDate") or "")
        items.append(
            TrendItem(
                title=title, url=url, source=source, summary=summary, published_at=published
            )
        )
    return items


def _parse_atom(root: ET.Element, source_hint: str) -> list[TrendItem]:
    source = (root.findtext(f"{_ATOM_NS}title") or source_hint).strip()
    items: list[TrendItem] = []
    for entry in root.findall(f"{_ATOM_NS}entry"):
        title = (entry.findtext(f"{_ATOM_NS}title") or "").strip()
        link = entry.find(f"{_ATOM_NS}link")
        url = (link.get("href") or "").strip() if link is not None else ""
        if not title or not url:
            continue
        summary = _truncate(
            _strip_html(
                entry.findtext(f"{_ATOM_NS}summary") or entry.findtext(f"{_ATOM_NS}content") or ""
            )
        )
        published = _parse_iso(
            entry.findtext(f"{_ATOM_NS}updated") or entry.findtext(f"{_ATOM_NS}published") or ""
        )
        items.append(
            TrendItem(
                title=title, url=url, source=source, summary=summary, published_at=published
            )
        )
    return items


def parse_feed(xml_text: str, *, source_hint: str) -> list[TrendItem]:
    """RSS2.0/AtomのXML文字列をTrendItemへ解析する(不正XMLは空リスト)。"""
    # 外部フィードを解析するため、XXE・billion-laughs対策済みのdefusedxmlを使う。
    try:
        root = SafeET.fromstring(xml_text)
    except Exception:  # noqa: BLE001 - ParseError/EntitiesForbidden等、解析不能は全て空リスト
        return []
    if root.tag == "rss":
        return _parse_rss2(root, source_hint)
    if root.tag == f"{_ATOM_NS}feed":
        return _parse_atom(root, source_hint)
    return []


class RSSTrendProvider:
    """設定されたフィードURL群から最新記事を取得するプロバイダー。"""

    def __init__(self, settings: Settings) -> None:
        self._feed_urls = [
            url.strip() for url in settings.TREND_FEED_URLS.split(",") if url.strip()
        ]

    async def fetch_latest(self, *, limit: int) -> list[TrendItem]:
        items: list[TrendItem] = []
        timeout = httpx.Timeout(_FETCH_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for feed_url in self._feed_urls:
                try:
                    response = await client.get(feed_url)
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    logger.warning("trend_feed_fetch_failed", feed_url=feed_url, error=str(exc))
                    continue
                items.extend(parse_feed(response.text, source_hint=feed_url))

        seen: set[str] = set()
        unique: list[TrendItem] = []
        for item in items:
            if item.url in seen:
                continue
            seen.add(item.url)
            unique.append(item)
        unique.sort(
            key=lambda i: i.published_at.timestamp() if i.published_at else 0.0, reverse=True
        )
        return unique[: max(0, limit)]
