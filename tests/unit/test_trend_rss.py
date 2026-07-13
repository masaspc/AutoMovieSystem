"""RSS/Atomパーサの検証(フィクスチャXML文字列のみ。ネットワーク不使用)。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.core.config import Settings
from app.providers.trends import rss as rss_module
from app.providers.trends.rss import RSSTrendProvider, parse_feed

RSS2_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>テストニュース</title>
  <item>
    <title>記事タイトル1</title>
    <link>https://example.com/a1</link>
    <description><![CDATA[<p>要約<b>本文</b>です。</p>]]></description>
    <pubDate>Mon, 13 Jul 2026 09:00:00 +0900</pubDate>
  </item>
  <item>
    <title>記事タイトル2</title>
    <link>https://example.com/a2</link>
    <description>短い要約2</description>
  </item>
</channel></rss>"""

ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atomフィード</title>
  <entry>
    <title>Atom記事</title>
    <link href="https://example.com/atom1"/>
    <summary>Atomの要約</summary>
    <updated>2026-07-13T09:00:00+09:00</updated>
  </entry>
</feed>"""


def test_parse_rss2_extracts_title_link_summary_and_strips_html() -> None:
    items = parse_feed(RSS2_XML, source_hint="fallback")
    assert len(items) == 2
    assert items[0].title == "記事タイトル1"
    assert items[0].url == "https://example.com/a1"
    assert items[0].source == "テストニュース"  # channel titleを優先
    assert "本文" in items[0].summary and "<" not in items[0].summary
    assert items[0].published_at is not None
    assert items[1].published_at is None


def test_parse_atom_extracts_entries() -> None:
    items = parse_feed(ATOM_XML, source_hint="fallback")
    assert len(items) == 1
    assert items[0].title == "Atom記事"
    assert items[0].url == "https://example.com/atom1"
    assert items[0].summary == "Atomの要約"


def test_parse_atom_prefers_alternate_link_over_self_link() -> None:
    xml = ATOM_XML.replace(
        '<link href="https://example.com/atom1"/>',
        '<link rel="self" href="https://example.com/feed-entry"/>'
        '<link rel="alternate" href="https://example.com/article"/>',
    )

    items = parse_feed(xml, source_hint="fallback")

    assert items[0].url == "https://example.com/article"


def test_parse_atom_does_not_use_content_as_summary() -> None:
    xml = ATOM_XML.replace("<summary>Atomの要約</summary>", "<content>記事本文です</content>")

    items = parse_feed(xml, source_hint="fallback")

    assert items[0].summary == ""


def test_parse_feed_skips_non_http_article_urls() -> None:
    xml = RSS2_XML.replace("https://example.com/a1", "javascript:alert(1)")

    items = parse_feed(xml, source_hint="fallback")

    assert [item.title for item in items] == ["記事タイトル2"]


def test_parse_feed_truncates_summary_to_200_chars() -> None:
    long_desc = "あ" * 500
    xml = RSS2_XML.replace("短い要約2", long_desc)
    items = parse_feed(xml, source_hint="x")
    assert len(items[1].summary) == 200  # 省略記号込みで200字
    assert items[1].summary.endswith("…")


def test_parse_feed_returns_empty_for_broken_xml() -> None:
    assert parse_feed("<not-xml", source_hint="x") == []
    assert parse_feed("<html><body>not a feed</body></html>", source_hint="x") == []


def test_parse_feed_rejects_xxe_entity_expansion() -> None:
    """defusedxmlがDTD/外部実体を拒否する(XXE・billion-laughs対策)。"""
    xxe = (
        '<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        '<rss version="2.0"><channel><title>&x;</title></channel></rss>'
    )
    assert parse_feed(xxe, source_hint="x") == []


@pytest.mark.parametrize("invalid_xml", ["<not-xml", "<html><body>not a feed</body></html>"])
def test_rss_provider_warns_per_invalid_feed_and_keeps_successes(
    invalid_xml: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        _env_file=None,
        TREND_PROVIDER="rss",
        TREND_FEED_URLS="https://feeds.example/broken,https://feeds.example/ok",
    )  # type: ignore[call-arg]
    provider = RSSTrendProvider(settings)

    async def fake_get(_client: httpx.AsyncClient, url: str) -> httpx.Response:
        body = invalid_xml if url.endswith("/broken") else RSS2_XML
        return httpx.Response(200, text=body, request=httpx.Request("GET", url))

    warning_events: list[str] = []
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(
        rss_module.logger,
        "warning",
        lambda event, **_kwargs: warning_events.append(event),
    )

    items = asyncio.run(provider.fetch_latest(limit=20))

    assert len(items) == 2
    assert warning_events == ["trend_feed_parse_failed"]


def test_rss_provider_skips_unreachable_feed_and_keeps_successes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        TREND_PROVIDER="rss",
        TREND_FEED_URLS="https://feeds.example/down,https://feeds.example/ok",
    )  # type: ignore[call-arg]
    provider = RSSTrendProvider(settings)

    async def fake_get(_client: httpx.AsyncClient, url: str) -> httpx.Response:
        request = httpx.Request("GET", url)
        if url.endswith("/down"):
            raise httpx.ConnectError("unreachable", request=request)
        return httpx.Response(200, text=RSS2_XML, request=request)

    warning_events: list[str] = []
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(
        rss_module.logger,
        "warning",
        lambda event, **_kwargs: warning_events.append(event),
    )

    items = asyncio.run(provider.fetch_latest(limit=20))

    assert len(items) == 2
    assert warning_events == ["trend_feed_fetch_failed"]
