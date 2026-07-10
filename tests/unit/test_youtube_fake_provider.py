"""FakeYouTubeProvider: アップロード -> list_recent_uploads突合の一連フロー(ADR-0005)。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.providers.youtube.base import UploadRequest
from app.providers.youtube.factory import get_youtube_provider
from app.providers.youtube.fake import FakeYouTubeProvider, FakeYouTubeProviderStore


def _request(marker: str, *, title: str = "タイトル") -> UploadRequest:
    return UploadRequest(
        file_path="C:/fake/video.mp4",
        title=title,
        description="説明",
        tags=["tag1", "tag2"],
        privacy_status="private",
        idempotency_marker=marker,
    )


def test_upload_then_list_recent_uploads_contains_marker() -> None:
    provider = FakeYouTubeProvider()
    marker = "amx-idem:upload:project-1:checksum-abc"

    result = asyncio.run(provider.upload_video(request=_request(marker)))
    uploads = asyncio.run(provider.list_recent_uploads(max_results=10))

    assert result.youtube_video_id
    assert result.privacy_status == "private"
    matching = [u for u in uploads if u.youtube_video_id == result.youtube_video_id]
    assert len(matching) == 1
    assert marker in matching[0].description


def test_duplicate_upload_with_same_marker_creates_two_distinct_videos() -> None:
    """実YouTube同様、重複アップロードは受け付ける(重複防止はアプリ側reconcileの責務)。"""
    provider = FakeYouTubeProvider()
    marker = "amx-idem:upload:project-1:checksum-abc"

    result1 = asyncio.run(provider.upload_video(request=_request(marker)))
    result2 = asyncio.run(provider.upload_video(request=_request(marker)))

    assert result1.youtube_video_id != result2.youtube_video_id
    uploads = asyncio.run(provider.list_recent_uploads(max_results=10))
    matches = [u for u in uploads if marker in u.description]
    assert len(matches) == 2


def test_store_can_be_shared_across_provider_instances() -> None:
    """クラッシュ後の再試行(新しいプロバイダーインスタンスからreconcile)を再現できる。"""
    store = FakeYouTubeProviderStore()
    provider1 = FakeYouTubeProvider(store=store)
    marker = "amx-idem:upload:project-1:checksum-abc"
    result = asyncio.run(provider1.upload_video(request=_request(marker)))

    provider2 = FakeYouTubeProvider(store=store)
    uploads = asyncio.run(provider2.list_recent_uploads(max_results=10))

    assert any(u.youtube_video_id == result.youtube_video_id for u in uploads)


def test_factory_shares_store_across_requests() -> None:
    """管理画面の別リクエストでも、Fake投稿を予約できる。"""
    upload_provider = get_youtube_provider()
    uploaded = asyncio.run(
        upload_provider.upload_video(request=_request("amx-idem:factory-shared"))
    )

    schedule_provider = get_youtube_provider()
    publish_at = datetime(2026, 8, 1, tzinfo=UTC)
    asyncio.run(
        schedule_provider.set_schedule(
            youtube_video_id=uploaded.youtube_video_id,
            publish_at=publish_at,
        )
    )

    uploads = asyncio.run(schedule_provider.list_recent_uploads(max_results=10))
    assert any(upload.youtube_video_id == uploaded.youtube_video_id for upload in uploads)


def test_set_schedule_updates_stored_video() -> None:
    provider = FakeYouTubeProvider()
    marker = "amx-idem:upload:project-1:checksum-abc"
    result = asyncio.run(provider.upload_video(request=_request(marker)))

    publish_at = datetime(2026, 8, 1, tzinfo=UTC)
    asyncio.run(
        provider.set_schedule(youtube_video_id=result.youtube_video_id, publish_at=publish_at)
    )

    video = provider.store.videos[result.youtube_video_id]
    assert video.scheduled_at == publish_at


def test_check_auth_default_true_and_seedable() -> None:
    provider = FakeYouTubeProvider()
    assert asyncio.run(provider.check_auth()) is True

    provider.store.auth_ok = False
    assert asyncio.run(provider.check_auth()) is False


def test_seed_statistics_and_comments() -> None:
    from app.providers.youtube.base import CommentData, VideoStatistics

    provider = FakeYouTubeProvider()
    stats = VideoStatistics(
        youtube_video_id="vid-1",
        view_count=100,
        like_count=10,
        comment_count=2,
        collected_at=datetime.utcnow(),
    )
    provider.seed_statistics(stats)
    provider.seed_comments(
        "vid-1",
        [
            CommentData(
                youtube_comment_id="c-1",
                author_display_name="viewer",
                text="良い動画",
                published_at=datetime.utcnow(),
            )
        ],
    )

    result_stats = asyncio.run(provider.get_video_statistics(youtube_video_id="vid-1"))
    comments, next_token = asyncio.run(
        provider.list_comments(youtube_video_id="vid-1", page_token=None)
    )

    assert result_stats.view_count == 100
    assert len(comments) == 1
    assert next_token is None
