"""FakeYouTubeProvider(テスト・APIキー未設定環境向け: D-006)。実APIを一切呼ばない。

インメモリストア(`FakeYouTubeProviderStore`)を持つ。`upload_video` は呼び出しごとに
決定的だが一意なvideo_idを発行し、description末尾の idempotency マーカーを保存する。
実YouTube同様「重複アップロードも受け付ける」(同一マーカーでも新規動画として登録する)。
二重投稿防止はアプリ側 reconcile(`list_recent_uploads` + マーカー突合)の責務とする
(ADR-0005 / D-012)。

`FakeYouTubeProviderStore` はテスト間・複数インスタンス間で共有可能にし、
「クラッシュ後の再試行」シナリオ(新しいプロバイダーインスタンスから reconcile する)を
再現できるようにする。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.core.timeutil import utcnow_naive
from app.providers.youtube.base import (
    AudienceRetentionPoint,
    CommentData,
    UploadedVideoInfo,
    UploadRequest,
    UploadResult,
    VideoStatistics,
)


@dataclass
class _StoredVideo:
    youtube_video_id: str
    title: str
    description: str
    tags: list[str]
    privacy_status: str
    scheduled_at: datetime | None
    made_for_kids: bool
    contains_synthetic_media: bool
    created_at: datetime


class FakeYouTubeProviderStore:
    """`FakeYouTubeProvider` インスタンス間で共有可能なインメモリストア。"""

    def __init__(self) -> None:
        self.videos: dict[str, _StoredVideo] = {}
        self.statistics: dict[str, VideoStatistics] = {}
        self.comments: dict[str, list[CommentData]] = {}
        self.retention: dict[str, list[AudienceRetentionPoint]] = {}
        # `set_thumbnail` の呼び出し記録(youtube_video_id -> image_path文字列)。
        self.thumbnails: dict[str, str] = {}
        self.auth_ok: bool = True
        self._upload_counter: int = 0

    def reset(self) -> None:
        self.videos.clear()
        self.statistics.clear()
        self.comments.clear()
        self.retention.clear()
        self.thumbnails.clear()
        self.auth_ok = True
        self._upload_counter = 0

    def _next_sequence(self) -> int:
        self._upload_counter += 1
        return self._upload_counter


@dataclass
class FakeYouTubeProvider:
    """`YouTubeProvider` Protocol を満たすFake実装。テストからシード可能。"""

    store: FakeYouTubeProviderStore = field(default_factory=FakeYouTubeProviderStore)

    async def upload_video(self, *, request: UploadRequest) -> UploadResult:
        # 実YouTube同様、重複アップロード(同一idempotency_marker)も新規動画として
        # 受け付ける。二重投稿防止はアプリ側reconcileの責務(ADR-0005)。
        seq = self.store._next_sequence()  # noqa: SLF001 - 同一モジュール内の協調クラス
        seed = hashlib.sha256(
            f"{request.file_path}|{request.title}|{request.idempotency_marker}|{seq}".encode()
        ).hexdigest()
        youtube_video_id = f"fake{seed[:11]}"

        description_with_marker = f"{request.description}\n\n{request.idempotency_marker}"
        stored = _StoredVideo(
            youtube_video_id=youtube_video_id,
            title=request.title,
            description=description_with_marker,
            tags=list(request.tags),
            privacy_status=request.privacy_status,
            scheduled_at=request.scheduled_at,
            made_for_kids=request.made_for_kids,
            contains_synthetic_media=request.contains_synthetic_media,
            created_at=utcnow_naive(),
        )
        self.store.videos[youtube_video_id] = stored
        return UploadResult(
            youtube_video_id=youtube_video_id, privacy_status=request.privacy_status
        )

    async def set_thumbnail(self, *, youtube_video_id: str, image_path: Path) -> None:
        if youtube_video_id not in self.store.videos:
            raise ValueError(f"video not found: {youtube_video_id}")
        self.store.thumbnails[youtube_video_id] = str(image_path)

    @property
    def thumbnails(self) -> dict[str, str]:
        """呼び出し記録の参照用ショートカット(仕様上の `self.thumbnails`)。"""
        return self.store.thumbnails

    async def list_recent_uploads(self, *, max_results: int) -> list[UploadedVideoInfo]:
        videos = sorted(self.store.videos.values(), key=lambda v: v.created_at, reverse=True)
        return [
            UploadedVideoInfo(
                youtube_video_id=v.youtube_video_id,
                description=v.description,
                created_at=v.created_at,
                tags=list(v.tags),
            )
            for v in videos[:max_results]
        ]

    async def set_schedule(self, *, youtube_video_id: str, publish_at: datetime) -> None:
        video = self.store.videos.get(youtube_video_id)
        if video is None:
            raise ValueError(f"video not found: {youtube_video_id}")
        video.scheduled_at = publish_at
        # publishAtを設定する場合、YouTube仕様上 privacyStatus は private である必要がある。
        video.privacy_status = "private"

    async def get_video_statistics(self, *, youtube_video_id: str) -> VideoStatistics:
        stats = self.store.statistics.get(youtube_video_id)
        if stats is not None:
            return stats
        return VideoStatistics(
            youtube_video_id=youtube_video_id,
            view_count=0,
            like_count=0,
            comment_count=0,
            collected_at=utcnow_naive(),
        )

    async def get_audience_retention(
        self, *, youtube_video_id: str
    ) -> list[AudienceRetentionPoint]:
        return list(self.store.retention.get(youtube_video_id, []))

    async def list_comments(
        self, *, youtube_video_id: str, page_token: str | None
    ) -> tuple[list[CommentData], str | None]:
        comments = self.store.comments.get(youtube_video_id, [])
        return list(comments), None

    async def check_auth(self) -> bool:
        return self.store.auth_ok

    # --- テスト用シードヘルパー(実APIには存在しない拡張) ---

    def seed_statistics(self, stats: VideoStatistics) -> None:
        self.store.statistics[stats.youtube_video_id] = stats

    def seed_comments(self, youtube_video_id: str, comments: list[CommentData]) -> None:
        self.store.comments[youtube_video_id] = comments

    def seed_retention(self, youtube_video_id: str, points: list[AudienceRetentionPoint]) -> None:
        self.store.retention[youtube_video_id] = list(points)
