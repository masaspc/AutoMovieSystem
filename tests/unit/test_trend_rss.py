"""RSS/Atomパーサの検証(フィクスチャXML文字列のみ。ネットワーク不使用)。"""

from __future__ import annotations

from app.providers.trends.rss import parse_feed

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


def test_parse_feed_truncates_summary_to_200_chars() -> None:
    long_desc = "あ" * 500
    xml = RSS2_XML.replace("短い要約2", long_desc)
    items = parse_feed(xml, source_hint="x")
    assert len(items[1].summary) <= 201  # 200字+省略記号
    assert items[1].summary.endswith("…")


def test_parse_feed_returns_empty_for_broken_xml() -> None:
    assert parse_feed("<not-xml", source_hint="x") == []
    assert parse_feed("<html><body>not a feed</body></html>", source_hint="x") == []


def test_parse_feed_rejects_xxe_entity_expansion() -> None:
    """defusedxmlがDTD/外部実体を拒否する(XXE・billion-laughs対策)。"""
    xxe = (
        '<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        "<rss version=\"2.0\"><channel><title>&x;</title></channel></rss>"
    )
    assert parse_feed(xxe, source_hint="x") == []
