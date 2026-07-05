"""RealYouTubeProvider: エラー分類・リクエスト構築の単体テスト。

実YouTube APIは一切呼ばない。`googleapiclient.errors.HttpError` と
`googleapiclient.http.HttpRequest`/`Resource` はモックする。
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from app.providers.youtube.base import (
    AuthError,
    QuotaExceededError,
    TransientAPIError,
    UploadRequest,
)
from app.providers.youtube.real import build_video_body, classify_http_error


def _http_error(status: int, reason: str | None = None) -> HttpError:
    body = b"{}"
    if reason is not None:
        body = (
            f'{{"error": {{"message": "error", '
            f'"errors": [{{"domain": "youtube.quota", "reason": "{reason}"}}]}}}}'
        ).encode()
    resp = httplib2.Response({"status": str(status)})
    resp.status = status
    return HttpError(resp, body)


def test_classify_quota_exceeded_error() -> None:
    exc = _http_error(403, reason="quotaExceeded")
    classified = classify_http_error(exc)
    assert isinstance(classified, QuotaExceededError)


def test_classify_auth_error_401() -> None:
    exc = _http_error(401)
    classified = classify_http_error(exc)
    assert isinstance(classified, AuthError)


def test_classify_auth_error_403_non_quota() -> None:
    exc = _http_error(403, reason="forbidden")
    classified = classify_http_error(exc)
    assert isinstance(classified, AuthError)


def test_classify_rate_limit_as_transient() -> None:
    exc = _http_error(429, reason="rateLimitExceeded")
    classified = classify_http_error(exc)
    assert isinstance(classified, TransientAPIError)


def test_classify_server_error_as_transient() -> None:
    exc = _http_error(500)
    classified = classify_http_error(exc)
    assert isinstance(classified, TransientAPIError)


def test_build_video_body_reflects_privacy_tags_and_ai_disclosure() -> None:
    request = UploadRequest(
        file_path="video.mp4",
        title="タイトル",
        description="説明",
        tags=["a", "b"],
        privacy_status="unlisted",
        idempotency_marker="amx-idem:upload:p:c",
        made_for_kids=True,
        contains_synthetic_media=True,
    )

    body = build_video_body(request)

    assert body["snippet"]["title"] == "タイトル"
    assert body["snippet"]["tags"] == ["a", "b"]
    assert "amx-idem:upload:p:c" in body["snippet"]["description"]
    assert body["status"]["privacyStatus"] == "unlisted"
    assert body["status"]["selfDeclaredMadeForKids"] is True
    assert body["status"]["containsSyntheticMedia"] is True
    assert "publishAt" not in body["status"]


def test_build_video_body_publish_at_forces_private() -> None:
    """publishAt指定時は仕様上privacyStatusをprivateにする必要がある。"""
    scheduled = datetime(2026, 8, 1, 9, 0, 0, tzinfo=UTC)
    request = UploadRequest(
        file_path="video.mp4",
        title="タイトル",
        description="説明",
        tags=[],
        privacy_status="public",
        idempotency_marker="amx-idem:upload:p:c",
        scheduled_at=scheduled,
    )

    body = build_video_body(request)

    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["publishAt"] == "2026-08-01T09:00:00.000Z"


def test_upload_uses_resumable_media_file_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    """resumable uploadで next_chunk() をループすることを検証する(googleapiclientはモック)。"""
    import asyncio

    from app.core.config import Settings
    from app.providers.youtube import real as real_module

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    provider = real_module.RealYouTubeProvider(settings)

    mock_credentials = MagicMock()
    monkeypatch.setattr(provider, "_build_credentials", lambda: mock_credentials)

    mock_insert_request = MagicMock()
    mock_insert_request.next_chunk.return_value = (
        None,
        {"id": "yt-video-1", "status": {"privacyStatus": "private"}},
    )
    mock_youtube = MagicMock()
    mock_youtube.videos.return_value.insert.return_value = mock_insert_request
    monkeypatch.setattr(real_module, "build", lambda *a, **kw: mock_youtube)

    request = UploadRequest(
        file_path=__file__,  # 実在するファイルパスであればよい(MediaFileUploadの生成のみ)
        title="タイトル",
        description="説明",
        tags=[],
        privacy_status="private",
        idempotency_marker="amx-idem:upload:p:c",
    )

    result = asyncio.run(provider.upload_video(request=request))

    assert result.youtube_video_id == "yt-video-1"
    assert result.privacy_status == "private"
    mock_insert_request.next_chunk.assert_called_once()


def test_upload_classifies_quota_exceeded_and_does_not_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from app.core.config import Settings
    from app.providers.youtube import real as real_module

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    provider = real_module.RealYouTubeProvider(settings)
    monkeypatch.setattr(provider, "_build_credentials", lambda: MagicMock())

    call_count = {"n": 0}

    def _raise_quota() -> None:
        call_count["n"] += 1
        raise _http_error(403, reason="quotaExceeded")

    mock_insert_request = MagicMock()
    mock_insert_request.next_chunk.side_effect = _raise_quota
    mock_youtube = MagicMock()
    mock_youtube.videos.return_value.insert.return_value = mock_insert_request
    monkeypatch.setattr(real_module, "build", lambda *a, **kw: mock_youtube)

    request = UploadRequest(
        file_path=__file__,
        title="タイトル",
        description="説明",
        tags=[],
        privacy_status="private",
        idempotency_marker="amx-idem:upload:p:c",
    )

    with pytest.raises(QuotaExceededError):
        asyncio.run(provider.upload_video(request=request))

    assert call_count["n"] == 1  # リトライしない
