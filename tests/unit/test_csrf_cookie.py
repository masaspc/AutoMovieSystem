"""CSRF CookieのSecure属性(自動判定と CSRF_COOKIE_SECURE 明示上書き)のテスト。"""

from __future__ import annotations

import pytest
from fastapi import Response

from app.core.config import get_settings
from app.core.csrf import set_csrf_cookie

pytestmark = pytest.mark.usefixtures("_clear_settings_cache")


def _set_cookie_header(monkeypatch: pytest.MonkeyPatch, *, app_env: str, override: str) -> str:
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("CSRF_COOKIE_SECURE", override)
    get_settings.cache_clear()
    response = Response()
    set_csrf_cookie(response, "token-value")
    return response.headers["set-cookie"]


def test_development_defaults_to_not_secure(monkeypatch: pytest.MonkeyPatch) -> None:
    header = _set_cookie_header(monkeypatch, app_env="development", override="")
    assert "Secure" not in header
    assert "HttpOnly" in header
    assert "SameSite=strict" in header


def test_production_defaults_to_secure(monkeypatch: pytest.MonkeyPatch) -> None:
    header = _set_cookie_header(monkeypatch, app_env="production", override="")
    assert "Secure" in header


def test_production_can_disable_secure_for_plain_http_lan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    header = _set_cookie_header(monkeypatch, app_env="production", override="false")
    assert "Secure" not in header


def test_development_can_force_secure(monkeypatch: pytest.MonkeyPatch) -> None:
    header = _set_cookie_header(monkeypatch, app_env="development", override="true")
    assert "Secure" in header
