# セルフレビュー改善ループ + トレンド即応動画化 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 投稿後の反応からLLMが改善点を抽出し次回台本へ自動反映するループと、RSSから1クリックでニュース解説Shortを一括制作するトレンド即応機能を追加する。

**Architecture:** 既存のProvider抽象化(`app/providers/`)・Insight基盤・`produce_video_task`を再利用する。新規はTrendProvider(fake/rss)、trends service、self_reviewサービス、/trendsページ、Celeryタスク2種のみ。

**Tech Stack:** FastAPI+Jinja2、SQLAlchemy 2、Celery、httpx、defusedxml(XXE/billion-laughs対策済みXMLパーサ。新規依存はこれのみ)

## Global Constraints(spec準拠・全タスク共通)

- テストで実API/ネットワークを呼ばない(TrendProviderの既定はfake)
- 外部記事は「見出し+リンク+要約200字」のみ使用。本文の取得・転載禁止
- 人間承認・private投稿のfail-closedゲートを変更しない
- 状態遷移は`app/services/state_machine.py`経由のみ(orchestrationの既存ヘルパー利用)
- LLM呼び出しは`app/services/llm_gateway.call_llm`経由(UsageRecord・予算管理を自動適用)
- 各タスク完了時に `uv run ruff check . && uv run mypy app && uv run pytest tests/unit -q` を実行して緑を確認してからコミット
- コミットメッセージ末尾: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: TrendProvider基盤(base/fake/factory+設定)

**Files:**
- Create: `app/providers/trends/__init__.py`(空)
- Create: `app/providers/trends/base.py`
- Create: `app/providers/trends/fake.py`
- Create: `app/providers/trends/factory.py`
- Modify: `app/core/config.py`(`SE_ASSETS_DIR`の直後に3設定を追加)
- Modify: `.env.example`(`SE_ASSETS_DIR=assets/se`の直後に追記)
- Test: `tests/unit/test_trend_providers.py`

**Interfaces:**
- Produces: `TrendItem(title: str, url: str, source: str, summary: str, published_at: datetime | None)`(frozen dataclass)、`TrendProvider` Protocol(`async def fetch_latest(self, *, limit: int) -> list[TrendItem]`)、`get_trend_provider(settings=None) -> TrendProvider`、Settings属性 `TREND_PROVIDER: str = "fake"` / `TREND_FEED_URLS: str = ""` / `TREND_FETCH_LIMIT: int = 20`

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/unit/test_trend_providers.py
"""TrendProvider(fake/factory)の検証。実ネットワークは使わない。"""

from __future__ import annotations

import asyncio

from app.core.config import Settings
from app.providers.trends.base import TrendItem, TrendProvider
from app.providers.trends.factory import get_trend_provider
from app.providers.trends.fake import FakeTrendProvider


def test_fake_provider_returns_deterministic_items() -> None:
    provider = FakeTrendProvider()
    first = asyncio.run(provider.fetch_latest(limit=3))
    second = asyncio.run(provider.fetch_latest(limit=3))
    assert first == second
    assert len(first) == 3
    assert all(isinstance(item, TrendItem) for item in first)
    assert all(item.url.startswith("https://") for item in first)


def test_fake_provider_respects_limit() -> None:
    provider = FakeTrendProvider()
    items = asyncio.run(provider.fetch_latest(limit=1))
    assert len(items) == 1


def test_factory_returns_fake_by_default() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    provider = get_trend_provider(settings)
    assert isinstance(provider, FakeTrendProvider)
    assert isinstance(provider, TrendProvider)


def test_factory_rejects_unknown_provider() -> None:
    import pytest

    settings = Settings(_env_file=None, TREND_PROVIDER="nope")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        get_trend_provider(settings)
```

- [ ] **Step 2: 失敗を確認** — `uv run pytest tests/unit/test_trend_providers.py -q` → ModuleNotFoundError

- [ ] **Step 3: 実装**

```python
# app/providers/trends/base.py
"""トレンド情報源の共通型・Protocol(D-026)。

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
```

```python
# app/providers/trends/fake.py
"""決定的なFakeトレンドプロバイダー(テスト・デモ用)。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.providers.trends.base import TrendItem

_FAKE_ITEMS: tuple[TrendItem, ...] = (
    TrendItem(
        title="新しい生成AIモデルが発表される",
        url="https://example.com/news/ai-model-release",
        source="Fake Tech News",
        summary="大手AI企業が新しい生成AIモデルを発表した。推論性能と日本語対応が強化されている。",
        published_at=datetime(2026, 7, 13, 9, 0, tzinfo=UTC),
    ),
    TrendItem(
        title="AIコーディング支援ツールの新機能",
        url="https://example.com/news/ai-coding-tools",
        source="Fake Dev Weekly",
        summary="開発者向けAIツールにエージェント機能が追加され、複数ファイルの編集が可能になった。",
        published_at=datetime(2026, 7, 13, 8, 0, tzinfo=UTC),
    ),
    TrendItem(
        title="動画生成AIの品質が大幅向上",
        url="https://example.com/news/video-generation",
        source="Fake Media Lab",
        summary="最新の動画生成AIは60秒の一貫した映像を生成できるようになった。",
        published_at=datetime(2026, 7, 13, 7, 0, tzinfo=UTC),
    ),
)


class FakeTrendProvider:
    """常に同じ3件を返す決定的Fake。"""

    async def fetch_latest(self, *, limit: int) -> list[TrendItem]:
        return list(_FAKE_ITEMS[: max(0, limit)])
```

```python
# app/providers/trends/factory.py
"""TrendProviderの選択(設定 `TREND_PROVIDER=fake|rss`)。"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.providers.trends.base import TrendProvider
from app.providers.trends.fake import FakeTrendProvider


def get_trend_provider(settings: Settings | None = None) -> TrendProvider:
    settings = settings or get_settings()
    if settings.TREND_PROVIDER == "fake":
        return FakeTrendProvider()
    if settings.TREND_PROVIDER == "rss":
        from app.providers.trends.rss import RSSTrendProvider

        return RSSTrendProvider(settings)
    raise ValueError(f"未知のTREND_PROVIDERです: {settings.TREND_PROVIDER!r} (fake|rss)")
```

config.py(`SE_ASSETS_DIR: str = "assets/se"` の直後):

```python
    # トレンド情報源(D-026)。既定fake=テスト・デモでネットワークを使わない。
    # rss指定時はTREND_FEED_URLS(カンマ区切りのRSS/AtomフィードURL)から取得する。
    TREND_PROVIDER: str = "fake"
    TREND_FEED_URLS: str = ""
    TREND_FETCH_LIMIT: int = 20
```

.env.example(`SE_ASSETS_DIR=assets/se` の直後):

```
# トレンド情報源(fake|rss)。rssはRSS/AtomフィードURLをカンマ区切りで指定
# 例: TREND_FEED_URLS=https://rss.itmedia.co.jp/rss/2.0/aiplus.xml,https://news.google.com/rss/search?q=生成AI&hl=ja&gl=JP&ceid=JP:ja
TREND_PROVIDER=fake
TREND_FEED_URLS=
TREND_FETCH_LIMIT=20
```

注: Task 2でrss.pyを作るまで、factoryのrss分岐はimportエラーになるためテストでは触れない。

- [ ] **Step 4: 緑を確認** — `uv run pytest tests/unit/test_trend_providers.py -q` → 4 passed
- [ ] **Step 5: コミット** — `feat: TrendProvider基盤(fake/factory+設定)`

---

### Task 2: RSSTrendProvider(RSS2.0/Atomパーサ)

**Files:**
- Create: `app/providers/trends/rss.py`
- Test: `tests/unit/test_trend_rss.py`
- 依存追加: `uv add defusedxml`(pyproject.toml/uv.lockが更新される)

**Interfaces:**
- Consumes: `TrendItem`, `TrendProvider`(Task 1)
- Produces: `RSSTrendProvider(settings)`(TrendProvider実装)、`parse_feed(xml_text: str, *, source_hint: str) -> list[TrendItem]`(純関数・テスト可能)

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/unit/test_trend_rss.py
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
```

- [ ] **Step 2: 失敗を確認** — `uv run pytest tests/unit/test_trend_rss.py -q` → ModuleNotFoundError

- [ ] **Step 3: 実装**

```python
# app/providers/trends/rss.py
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
```

- [ ] **Step 4: 緑を確認** — `uv run pytest tests/unit/test_trend_rss.py tests/unit/test_trend_providers.py -q`
- [ ] **Step 5: コミット** — `feat: RSSトレンドプロバイダー(RSS2.0/Atom対応・fail-soft)`

---

### Task 3: trends service(instant_videoize・URL重複防止)

**Files:**
- Create: `app/services/trends/__init__.py`(空)
- Create: `app/services/trends/service.py`
- Test: `tests/unit/test_trends_service.py`

**Interfaces:**
- Consumes: `TrendItem`(Task 1)、orchestrationヘルパー `_get_or_create_video_project` / `_advance_status`、`ProductionSettings`
- Produces: `instant_videoize(session, *, channel_id, title, url, summary, source) -> Topic`(既存URLなら既存Topicを返す)、`trend_source_ref(url) -> str`

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/unit/test_trends_service.py
"""トレンド即動画化サービスの検証(FakeのみでネットワークもLLMも不使用)。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.evidence import Evidence
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.schemas.production_settings import ProductionSettings
from app.services.trends.service import instant_videoize, trend_source_ref


def _make_channel(db_session: Session) -> Channel:
    channel = Channel(name="ch")
    db_session.add(channel)
    db_session.flush()
    return channel


def test_instant_videoize_creates_topic_evidence_and_configured_project(
    db_session: Session,
) -> None:
    channel = _make_channel(db_session)

    topic = instant_videoize(
        db_session,
        channel_id=channel.id,
        title="新しい生成AIモデルが発表",
        url="https://example.com/news/1",
        summary="推論性能が強化された。",
        source="Example News",
    )
    db_session.commit()

    assert topic.source_type == "trend"
    assert topic.source_ref == trend_source_ref("https://example.com/news/1")

    evidence = db_session.query(Evidence).filter(Evidence.topic_id == topic.id).one()
    assert evidence.source_url == "https://example.com/news/1"
    assert evidence.verification_status == "pending"

    project = (
        db_session.query(VideoProject).filter(VideoProject.topic_id == topic.id).one()
    )
    assert project.status == "RESEARCH_READY"
    settings = ProductionSettings.model_validate(project.production_settings)
    assert settings.preset == "short"
    assert settings.script_template == "news_commentary"
    assert settings.bgm_mood == "serious"


def test_instant_videoize_dedups_same_url(db_session: Session) -> None:
    channel = _make_channel(db_session)
    first = instant_videoize(
        db_session,
        channel_id=channel.id,
        title="同じ記事",
        url="https://example.com/dup",
        summary="要約",
        source="News",
    )
    db_session.commit()
    second = instant_videoize(
        db_session,
        channel_id=channel.id,
        title="同じ記事(再押下)",
        url="https://example.com/dup",
        summary="要約",
        source="News",
    )
    db_session.commit()

    assert first.id == second.id
    assert db_session.query(Topic).filter(Topic.source_type == "trend").count() == 1
```

- [ ] **Step 2: 失敗を確認** — `uv run pytest tests/unit/test_trends_service.py -q`

- [ ] **Step 3: 実装**

```python
# app/services/trends/service.py
"""トレンド記事のワンクリック動画化(D-026)。

Topic(source_type="trend")+Evidence(出典URL)+ニュース解説Short設定の
VideoProjectを作成し、既存の一括制作(produce_video_task)へ引き渡せる状態にする。
同一URLの二重動画化はsource_refで防止する。
"""

from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.evidence import Evidence
from app.models.topic import Topic
from app.schemas.production_settings import ProductionSettings
from app.services.orchestration import (
    _advance_status,  # noqa: SLF001 - オーケストレーションの該当ステップを再利用する
    _get_or_create_video_project,  # noqa: SLF001
)

logger = get_logger(__name__)

# トレンド動画の既定設定: スピード重視のニュース解説Short。
TREND_PRODUCTION_SETTINGS = ProductionSettings(
    preset="short", script_template="news_commentary", bgm_mood="serious"
)


def trend_source_ref(url: str) -> str:
    """URLから決定的な重複防止キーを作る。"""
    return "trend:" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def instant_videoize(
    session: Session,
    *,
    channel_id: str,
    title: str,
    url: str,
    summary: str,
    source: str,
) -> Topic:
    """トレンド記事からTopic+Evidence+設定済みVideoProjectを作成する(URL単位で冪等)。"""
    source_ref = trend_source_ref(url)
    existing = (
        session.query(Topic)
        .filter(Topic.channel_id == channel_id, Topic.source_ref == source_ref)
        .one_or_none()
    )
    if existing is not None:
        return existing

    topic = Topic(
        channel_id=channel_id,
        title=title,
        description=f"出典: {source} {url}",
        source_type="trend",
        source_ref=source_ref,
    )
    session.add(topic)
    session.flush()

    claim = summary or title
    session.add(
        Evidence(
            topic_id=topic.id,
            source_url=url,
            source_title=f"{source}: {title}"[:255],
            publisher=source[:255],
            claim=claim,
            excerpt_hash=hashlib.sha256(claim.encode("utf-8")).hexdigest(),
            verification_status="pending",
        )
    )

    project = _get_or_create_video_project(session, topic_id=topic.id)
    project.production_settings = TREND_PRODUCTION_SETTINGS.model_dump()
    _advance_status(project, "TOPIC_SCORED")
    _advance_status(project, "RESEARCH_READY")
    session.flush()

    logger.info("trend_topic_created", topic_id=topic.id, url=url)
    return topic
```

注: Evidenceモデルの必須カラムは`app/models/evidence.py`を確認し、`excerpt_hash`等の
実フィールド名に合わせること(orchestration._ensure_dummy_evidence が手本)。

- [ ] **Step 4: 緑を確認+コミット** — `feat: トレンド即動画化サービス(重複防止+Short設定)`

---

### Task 4: /trendsページ(web+ナビ)

**Files:**
- Create: `app/web/trends_page.py`
- Create: `app/templates/trends/list.html`
- Modify: `app/web/router.py`(import+include。`growth_router`の下に追加)
- Modify: `app/templates/base.html`(ナビ「グロース」内 `nav_item('/benchmarks', ...)` の下に `{{ nav_item('/trends', 'トレンド') }}`)
- Test: `tests/unit/test_web_trends.py`

**Interfaces:**
- Consumes: `get_trend_provider`(Task 1)、`instant_videoize`(Task 3)、`produce_video_task`(既存 `app/workers/tasks/production.py`)
- Produces: GET `/trends`、POST `/trends/videoize`(form: title/url/summary/source)

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/unit/test_web_trends.py
"""/trendsページの検証(FakeTrendProvider+dispatchモック)。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.topic import Topic


@dataclass
class _FakeAsyncResult:
    id: str


def _make_channel(db_session: Session) -> Channel:
    channel = Channel(name="ch")
    db_session.add(channel)
    db_session.commit()
    return channel


def test_trends_page_lists_fake_items(client: TestClient, db_session: Session) -> None:
    _make_channel(db_session)
    response = client.get("/trends")
    assert response.status_code == 200
    assert "新しい生成AIモデルが発表される" in response.text
    assert "即動画化" in response.text


def test_trends_page_without_channel_shows_guidance(
    client: TestClient, db_session: Session
) -> None:
    response = client.get("/trends")
    assert response.status_code == 200
    assert "チャンネル" in response.text  # チャンネル未登録の案内


def test_videoize_creates_topic_and_dispatches_production(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_channel(db_session)
    monkeypatch.setattr(
        "app.web.trends_page.produce_video_task.delay",
        lambda topic_id: _FakeAsyncResult(id="task-1"),
    )

    get_response = client.get("/trends")
    csrf_token = get_response.cookies["csrf_token"]

    response = client.post(
        "/trends/videoize",
        data={
            "csrf_token": csrf_token,
            "title": "新モデル発表",
            "url": "https://example.com/n1",
            "summary": "要約テキスト",
            "source": "Example News",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "task_id=task-1" in response.headers["location"]
    topic = db_session.query(Topic).filter(Topic.source_type == "trend").one()
    assert topic.title == "新モデル発表"
```

- [ ] **Step 2: 失敗を確認** — `uv run pytest tests/unit/test_web_trends.py -q`

- [ ] **Step 3: 実装**

```python
# app/web/trends_page.py
"""トレンド(最新情報)一覧+ワンクリック即動画化(D-026)。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.csrf import get_or_issue_csrf_token, set_csrf_cookie
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.channel import Channel
from app.providers.trends.factory import get_trend_provider
from app.services.trends.service import instant_videoize
from app.web.common import require_csrf
from app.workers.tasks.production import produce_video_task

logger = get_logger(__name__)

router = APIRouter(tags=["web-trends"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/trends", response_class=HTMLResponse)
async def list_trends(request: Request, db: DbSession) -> HTMLResponse:
    from app.core.config import get_settings

    settings = get_settings()
    provider = get_trend_provider(settings)
    items = await provider.fetch_latest(limit=settings.TREND_FETCH_LIMIT)
    channel = db.query(Channel).order_by(Channel.created_at.asc()).first()

    csrf_token = get_or_issue_csrf_token(request)
    templates = request.app.state.templates
    response = templates.TemplateResponse(
        request,
        "trends/list.html",
        {
            "items": items,
            "channel": channel,
            "provider_name": settings.TREND_PROVIDER,
            "csrf_token": csrf_token,
        },
    )
    set_csrf_cookie(response, csrf_token)
    return response


@router.post("/trends/videoize")
def videoize_trend(
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
    title: Annotated[str, Form(max_length=500)],
    url: Annotated[str, Form(max_length=2000)],
    summary: Annotated[str, Form(max_length=1000)] = "",
    source: Annotated[str, Form(max_length=255)] = "",
) -> RedirectResponse:
    """記事1件をTopic化し、一括制作(自動レビューまで)をdispatchする。"""
    require_csrf(request, csrf_token)
    channel = db.query(Channel).order_by(Channel.created_at.asc()).first()
    if channel is None:
        raise HTTPException(status_code=400, detail="チャンネルが未登録です")

    topic = instant_videoize(
        db, channel_id=channel.id, title=title, url=url, summary=summary, source=source
    )
    db.commit()

    task = produce_video_task.delay(topic.id)
    logger.info("trend_videoize_dispatched", topic_id=topic.id, task_id=task.id)
    redirect_url = f"/topics/{topic.id}?task_id={task.id}&task_label=トレンド一括制作"
    return RedirectResponse(url=redirect_url, status_code=303)
```

```html
{# app/templates/trends/list.html #}
{% extends "base.html" %}
{% block title %}トレンド | AutoMovie{% endblock %}
{% block content %}
<div class="page-header">
  <div>
    <h2>トレンド</h2>
    <p class="page-desc">RSSフィードの最新情報。気になる記事を1クリックでニュース解説Shortに(承認・投稿は従来通り)。</p>
  </div>
  <div class="actions"><span class="badge badge-neutral">source: {{ provider_name }}</span></div>
</div>

{% if channel is none %}
<div class="flash flash-warning">チャンネルが未登録のため動画化できません。先にチャンネルを作成してください。</div>
{% endif %}

{% if not items %}
<div class="card"><div class="empty">記事を取得できませんでした。TREND_FEED_URLS の設定と到達性を確認してください。</div></div>
{% endif %}

{% for item in items %}
<div class="card">
  <h3 class="card-title"><a href="{{ item.url }}" rel="noopener" target="_blank">{{ item.title }}</a></h3>
  <p class="muted" style="margin: 0 0 6px;">
    {{ item.source }}{% if item.published_at %} ・ {{ item.published_at.strftime("%m/%d %H:%M") }}{% endif %}
  </p>
  {% if item.summary %}<p style="margin: 0 0 10px;">{{ item.summary }}</p>{% endif %}
  <form class="inline" method="post" action="/trends/videoize">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}" />
    <input type="hidden" name="title" value="{{ item.title }}" />
    <input type="hidden" name="url" value="{{ item.url }}" />
    <input type="hidden" name="summary" value="{{ item.summary }}" />
    <input type="hidden" name="source" value="{{ item.source }}" />
    <button type="submit"{{ ' disabled' if channel is none else '' }}>⚡ 即動画化(Short)</button>
  </form>
</div>
{% endfor %}
{% endblock %}
```

router.py: `from app.web.trends_page import router as trends_router` を追加し、
`router.include_router(benchmarks_router)` の直後に `router.include_router(trends_router)`。
base.html: グロースのulに `{{ nav_item('/trends', 'トレンド') }}` を追加。

- [ ] **Step 4: 緑を確認+コミット** — `feat: /trendsページ(ワンクリック即動画化)`

---

### Task 5: SelfReviewスキーマ+self_reviewサービス

**Files:**
- Create: `app/schemas/self_review.py`
- Create: `app/services/feedback/self_review.py`
- Modify: `app/providers/llm/fake.py`(operation="self_review"ジェネレーター追加。既存の`_classify_comment`の直後に関数を追加し、operation分岐テーブルへ登録)
- Test: `tests/unit/test_self_review.py`

**Interfaces:**
- Consumes: `call_llm`、`run_idempotent_async`、`_upsert_insight`は使わず自前でInsight作成(UNIQUE制約: source_type+source_id+insight_type+source_ref)
- Produces: `SelfReviewReport{good_points: list[str], bad_points: list[str], lessons: list[SelfReviewLesson{finding: str, recommended_action: str}]}`、`run_self_review(session, *, publication_id, provider) -> list[Insight]`、`collect_recent_lessons(session, *, channel_id, limit=5) -> list[Insight]`、`SELF_REVIEW_INSIGHT_TYPE = "self_review"`

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/unit/test_self_review.py
"""投稿後セルフレビュー(LLM振り返り→Insight→次回反映)の検証。"""

from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.insight import Insight
from app.models.publication import Publication
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_metric_daily import VideoMetricDaily
from app.models.video_project import VideoProject
from app.providers.llm.fake import DeterministicFakeLLMProvider
from app.services.feedback.self_review import (
    SELF_REVIEW_INSIGHT_TYPE,
    collect_recent_lessons,
    run_self_review,
)


def _publication_with_metrics(db_session: Session) -> Publication:
    channel = Channel(name="ch")
    db_session.add(channel)
    db_session.flush()
    topic = Topic(channel_id=channel.id, title="t", source_type="manual", source_ref="r1")
    db_session.add(topic)
    db_session.flush()
    script = Script(
        topic_id=topic.id, version=1, title="タイトル",
        body={"sections": []}, source_manifest={}, status="reviewed",
    )
    db_session.add(script)
    db_session.flush()
    project = VideoProject(
        topic_id=topic.id, script_id=script.id, status="UPLOADED_PRIVATE", generation=1
    )
    db_session.add(project)
    db_session.flush()
    publication = Publication(
        video_project_id=project.id,
        idempotency_key="upload:x:1",
        title="タイトル",
        description="説明",
        privacy_status="private",
        upload_status="completed",
        youtube_video_id="vid-1",
    )
    db_session.add(publication)
    db_session.flush()
    db_session.add(
        VideoMetricDaily(
            publication_id=publication.id,
            metric_date=date(2026, 7, 12),
            views=100, impressions=1000, ctr=0.03,
            average_view_percentage=42.0, comments_count=3,
        )
    )
    db_session.commit()
    return publication


def test_run_self_review_persists_lessons_as_insights(db_session: Session) -> None:
    publication = _publication_with_metrics(db_session)
    provider = DeterministicFakeLLMProvider()

    insights = asyncio.run(
        run_self_review(db_session, publication_id=publication.id, provider=provider)
    )
    db_session.commit()

    assert insights, "lessonsがInsightとして保存されるはず"
    stored = (
        db_session.query(Insight)
        .filter(Insight.insight_type == SELF_REVIEW_INSIGHT_TYPE)
        .all()
    )
    assert len(stored) == len(insights)
    assert all(i.recommended_action for i in stored)
    assert all(i.source_id == publication.id for i in stored)


def test_run_self_review_is_idempotent_for_same_metric_date(db_session: Session) -> None:
    publication = _publication_with_metrics(db_session)
    provider = DeterministicFakeLLMProvider()
    asyncio.run(run_self_review(db_session, publication_id=publication.id, provider=provider))
    db_session.commit()
    count_first = db_session.query(Insight).count()
    asyncio.run(run_self_review(db_session, publication_id=publication.id, provider=provider))
    db_session.commit()
    assert db_session.query(Insight).count() == count_first


def test_run_self_review_skips_publication_without_metrics(db_session: Session) -> None:
    publication = _publication_with_metrics(db_session)
    db_session.query(VideoMetricDaily).delete()
    db_session.commit()
    provider = DeterministicFakeLLMProvider()
    insights = asyncio.run(
        run_self_review(db_session, publication_id=publication.id, provider=provider)
    )
    assert insights == []


def test_collect_recent_lessons_returns_channel_scoped_insights(db_session: Session) -> None:
    publication = _publication_with_metrics(db_session)
    provider = DeterministicFakeLLMProvider()
    asyncio.run(run_self_review(db_session, publication_id=publication.id, provider=provider))
    db_session.commit()

    channel = db_session.query(Channel).one()
    lessons = collect_recent_lessons(db_session, channel_id=channel.id, limit=5)
    assert lessons
    assert all(lesson.insight_type == SELF_REVIEW_INSIGHT_TYPE for lesson in lessons)
```

注: Publication/VideoMetricDailyの必須カラムは実モデル(`app/models/publication.py` /
`app/models/video_metric_daily.py`)を確認して合わせること(テスト側を修正してよい。
プロダクトの意味を変えないこと)。

- [ ] **Step 2: 失敗を確認**

- [ ] **Step 3: 実装**

```python
# app/schemas/self_review.py
"""投稿後セルフレビューのLLM構造化出力(D-026)。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SelfReviewLesson(BaseModel):
    """次回の動画づくりへ反映する改善点1件。"""

    finding: str = Field(min_length=1, max_length=500)
    recommended_action: str = Field(min_length=1, max_length=500)


class SelfReviewReport(BaseModel):
    """動画1本の振り返りレポート。"""

    good_points: list[str] = Field(default_factory=list, max_length=5)
    bad_points: list[str] = Field(default_factory=list, max_length=5)
    lessons: list[SelfReviewLesson] = Field(default_factory=list, max_length=5)
```

```python
# app/services/feedback/self_review.py
"""投稿後セルフレビュー(D-026)。

指標・場面別維持率・コメント分類からLLMが良かった点/悪かった点/改善レッスンを
抽出し、Insight(insight_type="self_review")として保存する。レッスンは
`collect_recent_lessons` 経由で次回の台本生成プロンプトへ自動注入される
(改善提案画面でInsightを削除すれば注入対象から外れる)。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.channel import Channel
from app.models.insight import Insight
from app.models.job_run import JobRun
from app.models.publication import Publication
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_metric_daily import VideoMetricDaily
from app.models.video_project import VideoProject
from app.providers.llm.base import LLMProvider
from app.schemas.self_review import SelfReviewReport
from app.services.feedback.insights import summarize_comment_categories
from app.services.jobs import run_idempotent_async
from app.services.llm_gateway import call_llm

logger = get_logger(__name__)

SELF_REVIEW_INSIGHT_TYPE = "self_review"
OPERATION = "self_review"
PROMPT_VERSION = "self_review_v1"
MODEL_POLICY = "mid"

_SYSTEM_PROMPT = (
    "あなたはYouTubeチャンネルの動画アナリストです。与えられた指標・維持率・"
    "コメント傾向から、この動画の良かった点・悪かった点を分析し、次回の動画制作で"
    "必ず実行すべき改善レッスン(最大5件)を抽出してください。"
    "レッスンは台本作家への具体的な指示文(recommended_action)として書いてください。"
    "根拠のない断定は避け、データに基づいて記述してください。"
)


def _latest_metric(session: Session, publication_id: str) -> VideoMetricDaily | None:
    return (
        session.query(VideoMetricDaily)
        .filter(VideoMetricDaily.publication_id == publication_id)
        .order_by(VideoMetricDaily.metric_date.desc())
        .first()
    )


def _build_user_prompt(
    session: Session, publication: Publication, metric: VideoMetricDaily
) -> str:
    project = session.get(VideoProject, publication.video_project_id)
    script = (
        session.get(Script, project.script_id) if project and project.script_id else None
    )
    categories = summarize_comment_categories(session, publication_id=publication.id)
    category_text = (
        ", ".join(f"{k}: {v}件" for k, v in categories.items()) if categories else "なし"
    )
    return (
        f"動画タイトル: {publication.title}\n"
        f"最新指標({metric.metric_date}): 再生数={metric.views}, "
        f"CTR={metric.ctr}, 平均視聴維持率={metric.average_view_percentage}%\n"
        f"コメント分類: {category_text}\n"
        f"台本のセクション数: {len(((script.body or {}).get('sections') or [])) if script else '不明'}\n"
    )


async def run_self_review(
    session: Session, *, publication_id: str, provider: LLMProvider
) -> list[Insight]:
    """投稿1件のセルフレビューを実行しInsightを保存する(指標日単位で冪等)。"""
    publication = session.get(Publication, publication_id)
    if publication is None:
        raise ValueError(f"Publication not found: {publication_id}")
    metric = _latest_metric(session, publication_id)
    if metric is None:
        logger.info("self_review_skipped_no_metrics", publication_id=publication_id)
        return []

    idempotency_key = f"self_review:{publication_id}:{metric.metric_date.isoformat()}"

    async def _do_review(job_run: JobRun) -> list[Insight]:
        result = await call_llm(
            session,
            provider,
            operation=OPERATION,
            prompt_version=PROMPT_VERSION,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_build_user_prompt(session, publication, metric),
            response_schema=SelfReviewReport,
            model_policy=MODEL_POLICY,
            idempotency_key=idempotency_key,
            job_run_id=job_run.id,
        )
        report = SelfReviewReport.model_validate(result.data)
        created: list[Insight] = []
        for index, lesson in enumerate(report.lessons):
            source_ref = f"{idempotency_key}:{index}"
            existing = (
                session.query(Insight)
                .filter(
                    Insight.source_type == "publication",
                    Insight.source_id == publication_id,
                    Insight.insight_type == SELF_REVIEW_INSIGHT_TYPE,
                    Insight.source_ref == source_ref,
                )
                .one_or_none()
            )
            if existing is not None:
                created.append(existing)
                continue
            insight = Insight(
                source_type="publication",
                source_id=publication_id,
                insight_type=SELF_REVIEW_INSIGHT_TYPE,
                source_ref=source_ref,
                finding=lesson.finding,
                evidence={
                    "metric_date": metric.metric_date.isoformat(),
                    "good_points": report.good_points,
                    "bad_points": report.bad_points,
                },
                confidence=0.5,
                recommended_action=lesson.recommended_action,
                human_review_reason="LLMによる自動振り返り(次回台本へ自動反映)",
            )
            session.add(insight)
            created.append(insight)
        session.flush()
        return created

    job_result = await run_idempotent_async(
        session,
        job_type="self_review",
        entity_type="publication",
        entity_id=publication_id,
        idempotency_key=idempotency_key,
        fn=_do_review,
    )
    if job_result.status == "skipped":
        return (
            session.query(Insight)
            .filter(
                Insight.source_type == "publication",
                Insight.source_id == publication_id,
                Insight.insight_type == SELF_REVIEW_INSIGHT_TYPE,
            )
            .all()
        )
    return job_result.result or []


def collect_recent_lessons(
    session: Session, *, channel_id: str, limit: int = 5
) -> list[Insight]:
    """チャンネル配下の直近セルフレビューレッスンを新しい順に返す(プロンプト注入用)。"""
    return (
        session.query(Insight)
        .join(Publication, Insight.source_id == Publication.id)
        .join(VideoProject, Publication.video_project_id == VideoProject.id)
        .join(Topic, VideoProject.topic_id == Topic.id)
        .join(Channel, Topic.channel_id == Channel.id)
        .filter(
            Channel.id == channel_id,
            Insight.insight_type == SELF_REVIEW_INSIGHT_TYPE,
        )
        .order_by(Insight.created_at.desc())
        .limit(limit)
        .all()
    )
```

fake.py へ operation="self_review" のジェネレーターを追加(既存のoperation分岐
テーブル/if分岐に登録。実装スタイルはファイル内の`_classify_comment`登録箇所を踏襲):

```python
def _self_review(
    seed: bytes, operation: str, user_prompt: str, schema: type[BaseModel]
) -> dict[str, Any]:
    del seed, operation, user_prompt, schema
    return {
        "good_points": ["フックで結論を先出しできていた"],
        "bad_points": ["コード画面が30秒以上続き離脱が増えた"],
        "lessons": [
            {
                "finding": "コード画面が長いと維持率が下がる",
                "recommended_action": "コード解説は1画面30秒以内に分割してください",
            },
            {
                "finding": "クイズ直前の維持率が高い",
                "recommended_action": "中盤に確認クイズを1問入れてください",
            },
        ],
    }
```

- [ ] **Step 4: 緑を確認+コミット** — `feat: 投稿後セルフレビュー(指標→LLM振り返り→Insight)`

---

### Task 6: 改善レッスンの台本プロンプト自動注入

**Files:**
- Modify: `app/services/scripts/generator.py`
- Test: `tests/unit/test_script_generator.py`(追加のみ。既存テストは変更しない)

**Interfaces:**
- Consumes: `collect_recent_lessons`(Task 5)
- Produces: `generate_script`実行時、チャンネルにself_reviewレッスンがあればシステムプロンプト末尾に「【過去動画の振り返りからの改善指示(必ず反映)】」ブロックが付く

- [ ] **Step 1: 失敗するテストを書く**(test_script_generator.py へ追記)

```python
def test_generate_script_prompt_includes_self_review_lessons(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """直近のセルフレビューレッスンが台本生成プロンプトへ自動注入される。"""
    # 既存テストのfixture流儀でtopic+channelを作成し、そのchannelに紐づく
    # self_review Insightを1件直接INSERTする(publication経由の紐付けは
    # Task 5のテストで担保済みのため、ここでは注入文字列のみ検証する)。
    #
    # captured_prompts: list[str] を用意し、DeterministicFakeLLMProviderの
    # generateをラップして system_prompt を記録するスタブに差し替え、
    # generate_script 実行後に
    # assert any("過去動画の振り返りからの改善指示" in p for p in captured_prompts)
    # assert any("コード解説は1画面30秒以内" in p for p in captured_prompts)
    ...
```

(実装時は既存 `test_script_generator.py` のプロンプト検証テストの流儀
=providerスタブでsystem_promptをキャプチャする方式に合わせて具体化する。
`...` のまま残すことは禁止)

- [ ] **Step 2: 失敗を確認**

- [ ] **Step 3: 実装**(generator.py)

`generate_script` 内でtopic取得後に:

```python
    lessons = collect_recent_lessons(session, channel_id=topic.channel_id, limit=5)
```

`_build_prompts(topic, evidence_list, production_settings, lessons=lessons)` へ引数追加し、
system_promptの末尾へ:

```python
    lessons_instruction = ""
    if lessons:
        lesson_lines = "\n".join(
            f"- {lesson.recommended_action}" for lesson in lessons
        )
        lessons_instruction = (
            "【過去動画の振り返りからの改善指示(必ず反映)】\n" + lesson_lines
        )
```

を`system_prompt`最終要素として連結する。importは
`from app.services.feedback.self_review import collect_recent_lessons`。
PROMPT_VERSIONは変更しない(注入は動的コンテンツでありLLMCacheはプロンプト
ハッシュで自然に分離される。既存topicの冪等キーも不変)。

- [ ] **Step 4: 緑を確認+コミット** — `feat: セルフレビューレッスンを台本生成へ自動注入`

---

### Task 7: セルフレビューのCeleryタスク+webボタン+日次beat

**Files:**
- Create: `app/workers/tasks/feedback.py`
- Modify: `app/workers/celery_app.py`(include追加+beat_scheduleに日次エントリ)
- Modify: `app/web/publications_page.py`(一覧の各行に「自己レビュー」ボタン用POSTルート追加)
- Modify: `app/templates/publications/list.html`(ボタン追加。ファイル実名は`app/templates/`配下を確認)
- Test: `tests/unit/test_web_self_review.py`

**Interfaces:**
- Consumes: `run_self_review`(Task 5)、既存のタスクバナー(`?task_id=&task_label=`)
- Produces: Celery `feedback.run_self_review`(publication_id) / `feedback.run_daily_self_reviews`()、POST `/publications/{publication_id}/self-review`

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/unit/test_web_self_review.py
"""セルフレビューボタン(dispatch)の検証。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# fixtureはtests/unit/test_self_review.pyの_publication_with_metricsを
# 移植して使用する(重複は許容。共通化はしない=テストの独立性優先)。


@dataclass
class _FakeAsyncResult:
    id: str


def test_self_review_button_dispatches_task(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    publication = _publication_with_metrics(db_session)
    monkeypatch.setattr(
        "app.web.publications_page.run_self_review_task.delay",
        lambda publication_id: _FakeAsyncResult(id="task-sr"),
    )
    get_response = client.get("/publications")
    csrf_token = get_response.cookies["csrf_token"]
    response = client.post(
        f"/publications/{publication.id}/self-review",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "task_id=task-sr" in response.headers["location"]
```

- [ ] **Step 2: 失敗を確認**

- [ ] **Step 3: 実装**

```python
# app/workers/tasks/feedback.py
"""セルフレビューCeleryタスク(薄いラッパー。ロジックは app.services.feedback.self_review)。"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from app.core.logging import get_logger
from app.core.timeutil import utcnow_naive
from app.db.session import SessionLocal
from app.models.publication import Publication
from app.models.video_metric_daily import VideoMetricDaily
from app.providers.llm.factory import get_llm_provider
from app.services.feedback.self_review import run_self_review
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="feedback.run_self_review")
def run_self_review_task(publication_id: str) -> int:
    session = SessionLocal()
    try:
        insights = asyncio.run(
            run_self_review(
                session, publication_id=publication_id, provider=get_llm_provider()
            )
        )
        session.commit()
        return len(insights)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(name="feedback.run_daily_self_reviews")
def run_daily_self_reviews() -> int:
    """直近2日以内の指標があるPublicationを走査して未レビュー分を実行する。

    冪等キー(publication+指標日)により同日の二重実行は起きない。
    """
    session = SessionLocal()
    reviewed = 0
    try:
        cutoff = (utcnow_naive() - timedelta(days=2)).date()
        publication_ids = [
            row[0]
            for row in (
                session.query(VideoMetricDaily.publication_id)
                .filter(VideoMetricDaily.metric_date >= cutoff)
                .distinct()
                .all()
            )
        ]
        provider = get_llm_provider()
        for publication_id in publication_ids:
            if session.get(Publication, publication_id) is None:
                continue
            insights = asyncio.run(
                run_self_review(session, publication_id=publication_id, provider=provider)
            )
            session.commit()
            reviewed += 1 if insights else 0
        return reviewed
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

celery_app.py: includeへ `"app.workers.tasks.feedback",` を追加し、beat_scheduleへ:

```python
    # 投稿後セルフレビュー(D-026): 指標のある投稿を日次で振り返り、次回台本へ反映する。
    "run-daily-self-reviews": {
        "task": "feedback.run_daily_self_reviews",
        "schedule": 86400.0,
    },
```

publications_page.py へルート追加(既存ルートの流儀に合わせる):

```python
@router.post("/publications/{publication_id}/self-review")
def self_review_route(
    publication_id: str, request: Request, db: DbSession, csrf_token: Annotated[str, Form()]
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    if db.get(Publication, publication_id) is None:
        raise HTTPException(status_code=404, detail="publication not found")
    task = run_self_review_task.delay(publication_id)
    return RedirectResponse(
        url=f"/publications?task_id={task.id}&task_label=自己レビュー", status_code=303
    )
```

importは `from app.workers.tasks.feedback import run_self_review_task`。
publications一覧GETルートに `task_id`/`task_label` クエリ受け取り+テンプレートへの
受け渡し+`task_banner`表示を追加(topics.pyのtopic_detailと同じパターン)。
一覧テンプレートの各行へ:

```html
<form class="inline" method="post" action="/publications/{{ publication.id }}/self-review">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}" />
  <button type="submit" class="btn-sm btn-secondary">自己レビュー</button>
</form>
```

- [ ] **Step 4: 緑を確認+コミット** — `feat: セルフレビューのタスク・ボタン・日次beat`

---

### Task 8: ドキュメント+全体検証+Docker反映

**Files:**
- Modify: `TASKS.md`(追加開発セクションへ2行)/ `DECISIONS.md`(D-026追記)/ `docs/operations.md`(トレンド運用・セルフレビュー運用の節)

**Steps:**
- [x] DECISIONS.md へ D-026 を追記: トレンド収集はRSS(見出し+リンク+要約のみ・本文転載なし・既定fake)、セルフレビューは日次自動+レッスン自動注入(Insight削除でオプトアウト)、即動画化はShort×news_commentary固定で一括制作まで(承認・投稿は人間のまま)
- [x] TASKS.md の追加開発セクションへ完了行を追記
- [x] docs/operations.md へ「トレンド即応の運用」「セルフレビューの運用(自動反映の止め方=Insight削除)」を追記
- [x] 全体検証: `uv run ruff check . && uv run mypy app && uv run pytest -q`(実FFmpeg環境でmedia/e2e含め494 passed・1 skipped、失敗0件)
- [ ] `docker compose up -d --build app worker beat` で反映し、`docker compose exec app sh -c "ls /app/app/providers/trends"` で新モジュール存在確認
- [ ] コミット+`git push origin feature/video-quality-and-ops`

## Self-Review結果

- spec全要件にタスク対応あり(RSS=T2、fake既定=T1、即動画化=T3/T4、セルフレビュー=T5、自動注入=T6、手動+日次=T7、docs=T8)
- 型整合: `TrendItem`/`instant_videoize`/`run_self_review`/`collect_recent_lessons` のシグネチャはタスク間で一致
- 注意点をタスク内に明記済み: Evidence/Publication/VideoMetricDailyの実カラム名は実装時にモデル定義と突合し、テスト側を実モデルへ合わせる(プロダクトの意味は変えない)
