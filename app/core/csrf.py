"""CSRF対策共通モジュール(docs/architecture.md セキュリティ要点)。

itsdangerous署名トークンをセッションCookie+hiddenフィールドの二重送信で検証する
(double-submit cookie)。鍵は `SECRET_ENCRYPTION_KEY` から導出する。未設定時は
開発用フォールバック鍵を使う(本番運用では必ず設定すること)。
"""

from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import get_settings

CSRF_COOKIE_NAME = "csrf_token"
CSRF_FORM_FIELD = "csrf_token"
CSRF_MAX_AGE_SECONDS = 3600
_CSRF_SALT = "csrf-token"
_DEV_FALLBACK_SECRET = "insecure-dev-secret-change-me"  # noqa: S105 - シークレットではなく開発用フォールバック定数


def _serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    secret = settings.SECRET_ENCRYPTION_KEY or _DEV_FALLBACK_SECRET
    return URLSafeTimedSerializer(secret, salt=_CSRF_SALT)


def issue_csrf_token() -> str:
    """署名済みCSRFトークンを発行する(Cookie+hiddenフィールド両方に同じ値を設定する)。"""
    return _serializer().dumps({"v": 1})


def verify_csrf(cookie_value: str | None, form_value: str | None) -> bool:
    """CookieとフォームのCSRFトークンが一致し、かつ署名が有効であることを検証する。"""
    if not cookie_value or not form_value or cookie_value != form_value:
        return False
    try:
        _serializer().loads(cookie_value, max_age=CSRF_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return False
    return True
