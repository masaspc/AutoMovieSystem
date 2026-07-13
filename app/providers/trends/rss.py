"""RSS2.0/Atomフィードのトレンドプロバイダー。

取得するのは title / link / description(HTMLタグ除去・200字) / pubDate のみ。
本文は取得しない(docs/content-policy.md)。フィード単位でfail-soft
(到達不可・不正XMLはログ警告してスキップし、取得できたフィード分だけ返す)。
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET  # noqa: S405 - 型・Element走査にのみ使用(解析はdefusedxml)
from datetime import datetime
from email.utils import parsedate_to_datetime

import httpx
from defusedxml import ElementTree as SafeET

from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.trends.base import (
    TREND_SOURCE_MAX_CHARS,
    TREND_SUMMARY_MAX_CHARS,
    TREND_TITLE_MAX_CHARS,
    TrendItem,
    normalize_trend_url,
    truncate_trend_text,
)

logger = get_logger(__name__)

_FETCH_TIMEOUT_SECONDS = 15.0
_TAG_RE = re.compile(r"<[^>]+>")
_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _strip_html(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text)).strip()


def _truncate(text: str) -> str:
    return truncate_trend_text(text, max_chars=TREND_SUMMARY_MAX_CHARS)


def _article_url(value: str) -> str:
    try:
        return normalize_trend_url(value)
    except ValueError:
        return ""


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
    source = truncate_trend_text(
        channel.findtext("title") or source_hint, max_chars=TREND_SOURCE_MAX_CHARS
    )
    items: list[TrendItem] = []
    for item in channel.findall("item"):
        title = truncate_trend_text(item.findtext("title") or "", max_chars=TREND_TITLE_MAX_CHARS)
        url = _article_url(item.findtext("link") or "")
        if not title or not url:
            continue
        summary = _truncate(_strip_html(item.findtext("description") or ""))
        published = _parse_rfc822(item.findtext("pubDate") or "")
        items.append(
            TrendItem(title=title, url=url, source=source, summary=summary, published_at=published)
        )
    return items


def _atom_article_url(entry: ET.Element) -> str:
    """Atomのalternateリンクを優先し、self/enclosure等は記事URLに使わない。"""
    links = entry.findall(f"{_ATOM_NS}link")
    candidates = [link for link in links if (link.get("rel") or "").lower() == "alternate"]
    candidates.extend(link for link in links if not (link.get("rel") or "").strip())
    for link in candidates:
        url = _article_url(link.get("href") or "")
        if url:
            return url
    return ""


def _parse_atom(root: ET.Element, source_hint: str) -> list[TrendItem]:
    source = truncate_trend_text(
        root.findtext(f"{_ATOM_NS}title") or source_hint,
        max_chars=TREND_SOURCE_MAX_CHARS,
    )
    items: list[TrendItem] = []
    for entry in root.findall(f"{_ATOM_NS}entry"):
        title = truncate_trend_text(
            entry.findtext(f"{_ATOM_NS}title") or "", max_chars=TREND_TITLE_MAX_CHARS
        )
        url = _atom_article_url(entry)
        if not title or not url:
            continue
        # Atom contentは記事本文を含む場合があるため使用せず、summaryだけを扱う。
        summary = _truncate(_strip_html(entry.findtext(f"{_ATOM_NS}summary") or ""))
        published = _parse_iso(
            entry.findtext(f"{_ATOM_NS}updated") or entry.findtext(f"{_ATOM_NS}published") or ""
        )
        items.append(
            TrendItem(title=title, url=url, source=source, summary=summary, published_at=published)
        )
    return items


def _parse_feed_result(xml_text: str, *, source_hint: str) -> tuple[list[TrendItem], str | None]:
    """解析結果と、フィード自体が不正な場合の理由を返す。"""
    # 外部フィードを解析するため、XXE・billion-laughs対策済みのdefusedxmlを使う。
    try:
        root = SafeET.fromstring(xml_text)
    except Exception as exc:  # noqa: BLE001 - ParseError/EntitiesForbidden等をfail-soft化
        return [], type(exc).__name__
    if root.tag == "rss":
        return _parse_rss2(root, source_hint), None
    if root.tag == f"{_ATOM_NS}feed":
        return _parse_atom(root, source_hint), None
    return [], "unsupported_feed_root"


def parse_feed(xml_text: str, *, source_hint: str) -> list[TrendItem]:
    """RSS2.0/AtomのXML文字列をTrendItemへ解析する(不正XMLは空リスト)。"""
    items, _error = _parse_feed_result(xml_text, source_hint=source_hint)
    return items


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
            for configured_url in self._feed_urls:
                try:
                    feed_url = normalize_trend_url(configured_url)
                except ValueError as exc:
                    logger.warning(
                        "trend_feed_url_invalid", feed_url=configured_url, error=str(exc)
                    )
                    continue
                try:
                    response = await client.get(feed_url)
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    logger.warning("trend_feed_fetch_failed", feed_url=feed_url, error=str(exc))
                    continue
                parsed_items, parse_error = _parse_feed_result(response.text, source_hint=feed_url)
                if parse_error is not None:
                    logger.warning(
                        "trend_feed_parse_failed",
                        feed_url=feed_url,
                        error=parse_error,
                    )
                    continue
                items.extend(parsed_items)

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
