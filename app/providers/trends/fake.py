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
