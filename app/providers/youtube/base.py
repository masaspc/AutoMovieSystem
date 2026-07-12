"""YouTubeプロバイダー共通の型・Protocol(仕様§9・docs/adr/0005)。

外部YouTube Data API v3 は本モジュールの `YouTubeProvider` Protocol を実装する形で
`app/providers/youtube/` 配下に抽象化する。テストでは実APIを呼ばず、
`fake.py` の `FakeYouTubeProvider` を使う(実装は `app/providers/youtube/` のみ)。

フィールドは videos.insert (YouTube Data API v3) の仕様に準拠する:
https://developers.google.com/youtube/v3/docs/videos/insert
- status.privacyStatus / status.publishAt / status.selfDeclaredMadeForKids
- status.containsSyntheticMedia(AI開示: 合成/改変メディアの申告)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

PRIVACY_STATUSES = ("private", "unlisted", "public")


@dataclass(frozen=True)
class UploadRequest:
    """`upload_video` への入力一式。"""

    file_path: str
    title: str
    description: str
    tags: list[str]
    privacy_status: str
    # ADR-0005: reconcile用の不可視マーカー(`amx-idem:{idempotency_key}` 形式)。
    # プロバイダー実装は description 末尾へ埋め込む。
    idempotency_marker: str
    scheduled_at: datetime | None = None
    made_for_kids: bool = False
    contains_synthetic_media: bool = False


@dataclass(frozen=True)
class UploadResult:
    youtube_video_id: str
    privacy_status: str


@dataclass(frozen=True)
class VideoStatistics:
    youtube_video_id: str
    view_count: int
    like_count: int
    comment_count: int
    collected_at: datetime


@dataclass(frozen=True)
class AudienceRetentionPoint:
    """動画内の相対位置ごとの視聴維持率。elapsed_ratioは0.0〜1.0。"""

    elapsed_ratio: float
    audience_watch_ratio: float
    relative_retention_performance: float


@dataclass(frozen=True)
class CommentData:
    youtube_comment_id: str
    author_display_name: str
    text: str
    published_at: datetime
    like_count: int = 0
    parent_id: str | None = None


@dataclass(frozen=True)
class UploadedVideoInfo:
    """`list_recent_uploads` の戻り値要素(ADR-0005 reconcile: description内マーカー突合用)。"""

    youtube_video_id: str
    description: str
    created_at: datetime
    tags: list[str] = field(default_factory=list)


class YouTubeProviderError(Exception):
    """YouTubeプロバイダー共通エラー基底。"""


class QuotaExceededError(YouTubeProviderError):
    """日次クォータ超過(403 quotaExceeded)。リトライ不可。呼び出し元で処理を停止する。"""


class AuthError(YouTubeProviderError):
    """認証・認可エラー(401/403、quotaExceeded以外)。リトライ不可。"""


class TransientAPIError(YouTubeProviderError):
    """一時的エラー(429 rateLimitExceeded・5xx等)。指数バックオフでリトライ可能。"""


@runtime_checkable
class YouTubeProvider(Protocol):
    """YouTubeプロバイダー共通インターフェース(仕様§9)。

    実装は `app/providers/youtube/` 配下のみ(fake.py / real.py)。
    呼び出し側は必ず `app/services/publishing/` 経由で使うこと。
    """

    async def upload_video(self, *, request: UploadRequest) -> UploadResult: ...

    async def set_thumbnail(self, *, youtube_video_id: str, image_path: Path) -> None: ...

    async def list_recent_uploads(self, *, max_results: int) -> list[UploadedVideoInfo]: ...

    async def set_schedule(self, *, youtube_video_id: str, publish_at: datetime) -> None: ...

    async def get_video_statistics(self, *, youtube_video_id: str) -> VideoStatistics: ...

    async def get_audience_retention(
        self, *, youtube_video_id: str
    ) -> list[AudienceRetentionPoint]: ...

    async def list_comments(
        self, *, youtube_video_id: str, page_token: str | None
    ) -> tuple[list[CommentData], str | None]: ...

    async def check_auth(self) -> bool: ...
