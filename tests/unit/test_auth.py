"""管理画面/API全体のHTTP Basic認証(修正1・D-019)のテスト。

`client`(tests/conftest.py)フィクスチャは既にAPP_ENV=testで認証をバイパスする
設定になっているため、本ファイルでは独自にアプリを構築しAPP_ENV/ADMIN_PASSWORDを
明示的に切り替えて検証する。
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.main import create_app


def _basic_auth_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@contextmanager
def _client_for(db_session: Session) -> Iterator[TestClient]:
    app = create_app()

    def _get_db_override() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_production_like_env_without_password_returns_401(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    get_settings.cache_clear()

    with _client_for(db_session) as client:
        response = client.get("/dashboard")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


def test_production_disables_openapi_and_interactive_docs(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse-battery-staple")
    get_settings.cache_clear()

    with _client_for(db_session) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_wrong_credentials_returns_401(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse-battery-staple")
    get_settings.cache_clear()

    with _client_for(db_session) as client:
        response = client.get("/dashboard", headers=_basic_auth_header("admin", "wrong-password"))

    assert response.status_code == 401


def test_correct_credentials_returns_200(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse-battery-staple")
    get_settings.cache_clear()

    with _client_for(db_session) as client:
        response = client.get(
            "/dashboard", headers=_basic_auth_header("admin", "correct-horse-battery-staple")
        )

    assert response.status_code == 200


def test_development_without_password_bypasses_auth(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    # delenv だと開発者ローカルの .env の値へフォールバックしてしまうため、
    # 「未設定」は空文字で表現する(auth.py は falsy 判定)。
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    get_settings.cache_clear()

    with _client_for(db_session) as client:
        response = client.get("/dashboard")

    assert response.status_code == 200


def test_health_always_returns_200_without_auth_even_in_production_like_env(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    get_settings.cache_clear()

    with _client_for(db_session) as client:
        response = client.get("/health")

    assert response.status_code == 200


def test_api_router_also_requires_auth(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    get_settings.cache_clear()

    with _client_for(db_session) as client:
        response = client.post("/api/video-projects/does-not-exist/upload")

    assert response.status_code == 401
