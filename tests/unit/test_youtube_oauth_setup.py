"""scripts/youtube_oauth_setup.py のロジック関数の単体テスト。

対話フロー(`main()`、ブラウザ操作を伴う `run_local_server`)はテストしない。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.oauth_token import OAuthToken
from scripts.youtube_oauth_setup import store_oauth_token


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", "test-encryption-key-not-real-000000")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_store_oauth_token_encrypts_refresh_token(db_session: Session) -> None:
    token = store_oauth_token(
        db_session,
        refresh_token="1//plain-refresh-token-value",
        access_token="ya29.plain-access-token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    db_session.commit()

    assert token.provider == "youtube"
    assert "1//plain-refresh-token-value" not in token.encrypted_refresh_token
    assert token.encrypted_access_token is not None
    assert "ya29.plain-access-token" not in token.encrypted_access_token

    stored = db_session.get(OAuthToken, token.id)
    assert stored is not None
    assert stored.scopes == ["https://www.googleapis.com/auth/youtube.upload"]


def test_store_oauth_token_without_access_token(db_session: Session) -> None:
    token = store_oauth_token(
        db_session,
        refresh_token="1//plain-refresh-token-value",
        access_token=None,
        scopes=[],
    )

    assert token.encrypted_access_token is None
