"""実YouTube Data API v3プロバイダー(google-api-python-client + google-auth)。

公式ドキュメント準拠:
- videos.insert(resumable upload): https://developers.google.com/youtube/v3/docs/videos/insert
- 再開可能アップロード: https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol
- OAuth 2.0(installed app / refresh token):
  https://developers.google.com/identity/protocols/oauth2/native-app

リフレッシュトークンは `OAuthToken.encrypted_refresh_token` を `app/core/crypto.py`
(Fernet)で復号して使う。エラー分類:
- 403 quotaExceeded -> `QuotaExceededError`(リトライ不可。呼び出し元で停止)
- 429 rateLimitExceeded / 5xx -> `TransientAPIError`(tenacityで指数バックオフ最大3回)
- 401 / 403(quotaExceeded以外) -> `AuthError`(リトライ不可)

テストでは実APIを呼ばない(googleapiclient/google-authをモックする)。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from sqlalchemy import desc
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import Settings
from app.core.crypto import decrypt_secret
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.oauth_token import OAuthToken
from app.providers.youtube.base import (
    AuthError,
    CommentData,
    QuotaExceededError,
    TransientAPIError,
    UploadedVideoInfo,
    UploadRequest,
    UploadResult,
    VideoStatistics,
    YouTubeProviderError,
)

logger = get_logger(__name__)

TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105 - URLであり秘密情報ではない
UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
FORCE_SSL_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
DEFAULT_SCOPES = (UPLOAD_SCOPE, FORCE_SSL_SCOPE, READONLY_SCOPE)

_MAX_RETRIES = 3
_YOUTUBE_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.000Z"


class OAuthTokenNotFoundError(YouTubeProviderError):
    """指定チャンネルの有効なOAuthTokenがDBに存在しない場合。"""


def _error_reason(exc: HttpError) -> str | None:
    """`HttpError` から `errors[].reason` を取り出す(公式クライアントの`error_details`経由)。"""
    try:
        details = exc.error_details
    except Exception:  # noqa: BLE001 - パース不能な場合はNone扱い
        details = None
    if details:
        for detail in details:
            if isinstance(detail, dict):
                reason = detail.get("reason")
                if reason:
                    return str(reason)
    return None


def classify_http_error(exc: HttpError) -> YouTubeProviderError:
    """`HttpError` を仕様上のエラークラスへ分類する(公式ドキュメントのエラー体系準拠)。"""
    status = exc.resp.status if exc.resp is not None else None
    reason = _error_reason(exc)

    if status == 403 and reason == "quotaExceeded":
        return QuotaExceededError(f"YouTube API quota exceeded: status={status} reason={reason}")
    if status in (401, 403):
        return AuthError(
            f"YouTube API authentication/authorization error: status={status} reason={reason}"
        )
    if status == 429 or (status is not None and status >= 500):
        return TransientAPIError(f"YouTube API transient error: status={status} reason={reason}")
    return TransientAPIError(f"YouTube API error: status={status} reason={reason}")


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, TransientAPIError)


def _run_with_retry[T](fn: Callable[[], T]) -> T:
    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def _call() -> T:
        try:
            return fn()
        except HttpError as exc:
            raise classify_http_error(exc) from exc

    return _call()


def build_video_body(request: UploadRequest) -> dict[str, Any]:
    """videos.insert のリクエストボディを構築する(テストで検証しやすいよう公開関数にする)。

    AI開示(`containsSyntheticMedia`)・子ども向け設定(`selfDeclaredMadeForKids`)・
    予約公開(`publishAt`)を反映する。`publishAt` 指定時は仕様上 `privacyStatus` を
    `private` にする必要がある。
    """
    description_with_marker = f"{request.description}\n\n{request.idempotency_marker}"
    status: dict[str, Any] = {
        "privacyStatus": request.privacy_status,
        "selfDeclaredMadeForKids": request.made_for_kids,
        "containsSyntheticMedia": request.contains_synthetic_media,
    }
    if request.scheduled_at is not None:
        status["publishAt"] = request.scheduled_at.strftime(_YOUTUBE_TIME_FORMAT)
        status["privacyStatus"] = "private"

    return {
        "snippet": {
            "title": request.title,
            "description": description_with_marker,
            "tags": list(request.tags),
        },
        "status": status,
    }


def _load_oauth_token(session: Any, *, channel_id: str | None) -> OAuthToken:
    query = session.query(OAuthToken).filter(OAuthToken.provider == "youtube")
    if channel_id is not None:
        query = query.filter(OAuthToken.channel_id == channel_id)
    token = query.order_by(desc(OAuthToken.updated_at)).first()
    if token is None:
        raise OAuthTokenNotFoundError(
            f"OAuthToken not found for provider=youtube channel_id={channel_id!r}"
        )
    return token


class RealYouTubeProvider:
    """`YouTubeProvider` Protocol を満たす実装。DBからOAuthTokenを都度読み出す。"""

    def __init__(self, settings: Settings, *, channel_id: str | None = None) -> None:
        self._settings = settings
        self._channel_id = channel_id

    def _build_credentials(self) -> Credentials:
        session = SessionLocal()
        try:
            token = _load_oauth_token(session, channel_id=self._channel_id)
            refresh_token = decrypt_secret(token.encrypted_refresh_token, settings=self._settings)
            scopes = token.scopes or list(DEFAULT_SCOPES)
        finally:
            session.close()

        return Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=TOKEN_URI,
            client_id=self._settings.YOUTUBE_OAUTH_CLIENT_ID,
            client_secret=self._settings.YOUTUBE_OAUTH_CLIENT_SECRET,
            scopes=scopes,
        )

    def _build_client(self) -> Resource:
        credentials = self._build_credentials()
        credentials.refresh(GoogleAuthRequest())
        return build("youtube", "v3", credentials=credentials, cache_discovery=False)

    # --- upload_video -----------------------------------------------------

    def _upload_video_sync(self, request: UploadRequest) -> UploadResult:
        youtube = self._build_client()
        body = build_video_body(request)
        media = MediaFileUpload(
            request.file_path,
            chunksize=self._settings.YOUTUBE_UPLOAD_CHUNK_SIZE_BYTES,
            resumable=True,
            mimetype="video/*",
        )

        insert_request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        def _next_chunk() -> Any:
            return insert_request.next_chunk()

        response: dict[str, Any] | None = None
        while response is None:
            _status, response = _run_with_retry(_next_chunk)

        return UploadResult(
            youtube_video_id=response["id"], privacy_status=response["status"]["privacyStatus"]
        )

    async def upload_video(self, *, request: UploadRequest) -> UploadResult:
        return await asyncio.to_thread(self._upload_video_sync, request)

    # --- list_recent_uploads ------------------------------------------------

    def _list_recent_uploads_sync(self, max_results: int) -> list[UploadedVideoInfo]:
        youtube = self._build_client()

        def _list_channels() -> Any:
            return youtube.channels().list(part="contentDetails", mine=True).execute()

        channels_response = _run_with_retry(_list_channels)
        items = channels_response.get("items", [])
        if not items:
            return []
        uploads_playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        def _list_playlist_items() -> Any:
            return (
                youtube.playlistItems()
                .list(part="snippet", playlistId=uploads_playlist_id, maxResults=max_results)
                .execute()
            )

        playlist_response = _run_with_retry(_list_playlist_items)
        results: list[UploadedVideoInfo] = []
        for item in playlist_response.get("items", []):
            snippet = item.get("snippet", {})
            resource_id = snippet.get("resourceId", {})
            published_at_raw = snippet.get("publishedAt")
            created_at = (
                datetime.strptime(published_at_raw, "%Y-%m-%dT%H:%M:%SZ")
                if published_at_raw
                else datetime.utcnow()
            )
            results.append(
                UploadedVideoInfo(
                    youtube_video_id=resource_id.get("videoId", ""),
                    description=snippet.get("description", ""),
                    created_at=created_at,
                )
            )
        return results

    async def list_recent_uploads(self, *, max_results: int) -> list[UploadedVideoInfo]:
        return await asyncio.to_thread(self._list_recent_uploads_sync, max_results)

    # --- set_schedule --------------------------------------------------------

    def _set_schedule_sync(self, youtube_video_id: str, publish_at: datetime) -> None:
        youtube = self._build_client()

        def _update() -> Any:
            return (
                youtube.videos()
                .update(
                    part="status",
                    body={
                        "id": youtube_video_id,
                        "status": {
                            "privacyStatus": "private",
                            "publishAt": publish_at.strftime(_YOUTUBE_TIME_FORMAT),
                        },
                    },
                )
                .execute()
            )

        _run_with_retry(_update)

    async def set_schedule(self, *, youtube_video_id: str, publish_at: datetime) -> None:
        await asyncio.to_thread(self._set_schedule_sync, youtube_video_id, publish_at)

    # --- get_video_statistics -------------------------------------------------

    def _get_video_statistics_sync(self, youtube_video_id: str) -> VideoStatistics:
        youtube = self._build_client()

        def _list_videos() -> Any:
            return youtube.videos().list(part="statistics", id=youtube_video_id).execute()

        response = _run_with_retry(_list_videos)
        items = response.get("items", [])
        stats = items[0].get("statistics", {}) if items else {}
        return VideoStatistics(
            youtube_video_id=youtube_video_id,
            view_count=int(stats.get("viewCount", 0)),
            like_count=int(stats.get("likeCount", 0)),
            comment_count=int(stats.get("commentCount", 0)),
            collected_at=datetime.utcnow(),
        )

    async def get_video_statistics(self, *, youtube_video_id: str) -> VideoStatistics:
        return await asyncio.to_thread(self._get_video_statistics_sync, youtube_video_id)

    # --- list_comments ---------------------------------------------------------

    def _list_comments_sync(
        self, youtube_video_id: str, page_token: str | None
    ) -> tuple[list[CommentData], str | None]:
        youtube = self._build_client()

        def _list_comment_threads() -> Any:
            return (
                youtube.commentThreads()
                .list(
                    part="snippet",
                    videoId=youtube_video_id,
                    pageToken=page_token,
                    textFormat="plainText",
                )
                .execute()
            )

        response = _run_with_retry(_list_comment_threads)
        comments: list[CommentData] = []
        for item in response.get("items", []):
            top_level = item.get("snippet", {}).get("topLevelComment", {})
            comment_snippet = top_level.get("snippet", {})
            published_at_raw = comment_snippet.get("publishedAt")
            published_at = (
                datetime.strptime(published_at_raw, "%Y-%m-%dT%H:%M:%SZ")
                if published_at_raw
                else datetime.utcnow()
            )
            comments.append(
                CommentData(
                    youtube_comment_id=top_level.get("id", ""),
                    author_display_name=comment_snippet.get("authorDisplayName", ""),
                    text=comment_snippet.get("textDisplay", ""),
                    published_at=published_at,
                    like_count=int(comment_snippet.get("likeCount", 0)),
                    parent_id=None,
                )
            )
        next_page_token = response.get("nextPageToken")
        return comments, next_page_token

    async def list_comments(
        self, *, youtube_video_id: str, page_token: str | None
    ) -> tuple[list[CommentData], str | None]:
        return await asyncio.to_thread(self._list_comments_sync, youtube_video_id, page_token)

    # --- check_auth ------------------------------------------------------------

    def _check_auth_sync(self) -> bool:
        try:
            credentials = self._build_credentials()
            credentials.refresh(GoogleAuthRequest())
        except OAuthTokenNotFoundError:
            return False
        except RefreshError:
            return False
        return True

    async def check_auth(self) -> bool:
        return await asyncio.to_thread(self._check_auth_sync)
