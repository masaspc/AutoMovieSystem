"""Fake/Real 双方が `YouTubeProvider` Protocol の契約を満たすことを検証する。

実APIは呼ばない。Real側は googleapiclient/google-auth をモックする。
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.providers.youtube.base import (
    UploadRequest,
    UploadResult,
    YouTubeProvider,
)
from app.providers.youtube.fake import FakeYouTubeProvider
from app.providers.youtube.real import RealYouTubeProvider


def _request() -> UploadRequest:
    return UploadRequest(
        file_path=__file__,  # 実在するファイルパスであればよい(MediaFileUploadの生成のみ)
        title="タイトル",
        description="説明",
        tags=["a"],
        privacy_status="private",
        idempotency_marker="amx-idem:upload:p:c",
    )


@pytest.mark.contract
@pytest.mark.parametrize("provider_name", ["fake", "real"])
def test_provider_satisfies_youtube_protocol(provider_name: str) -> None:
    if provider_name == "fake":
        provider: YouTubeProvider = FakeYouTubeProvider()
    else:
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        provider = RealYouTubeProvider(settings)

    assert isinstance(provider, YouTubeProvider)


@pytest.mark.contract
def test_fake_and_real_upload_video_return_same_result_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_provider = FakeYouTubeProvider()
    fake_result = asyncio.run(fake_provider.upload_video(request=_request()))
    assert isinstance(fake_result, UploadResult)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    real_provider = RealYouTubeProvider(settings)
    monkeypatch.setattr(real_provider, "_build_credentials", lambda: MagicMock())

    mock_insert_request = MagicMock()
    mock_insert_request.next_chunk.return_value = (
        None,
        {"id": "yt-1", "status": {"privacyStatus": "private"}},
    )
    mock_youtube = MagicMock()
    mock_youtube.videos.return_value.insert.return_value = mock_insert_request

    import app.providers.youtube.real as real_module

    monkeypatch.setattr(real_module, "build", lambda *a, **kw: mock_youtube)

    real_result = asyncio.run(real_provider.upload_video(request=_request()))
    assert isinstance(real_result, UploadResult)
    assert type(fake_result) is type(real_result)


@pytest.mark.contract
def test_fake_check_auth_returns_bool() -> None:
    provider = FakeYouTubeProvider()
    result = asyncio.run(provider.check_auth())
    assert isinstance(result, bool)
