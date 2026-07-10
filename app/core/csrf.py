"""CSRF対策共通モジュール(docs/architecture.md セキュリティ要点)。

itsdangerous署名トークンをセッションCookie+hiddenフィールドの二重送信で検証する
(double-submit cookie)。鍵は `SECRET_ENCRYPTION_KEY` から導出する。
development/test 以外の環境で鍵未設定の場合は fail-fast(起動後最初のCSRF利用時に
例外)とし、弱い固定鍵での本番運用を防ぐ。
"""

from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import get_settings

CSRF_COOKIE_NAME = "csrf_token"
CSRF_FORM_FIELD = "csrf_token"
CSRF_MAX_AGE_SECONDS = 3600
_CSRF_SALT = "csrf-token"
_DEV_FALLBACK_SECRET = "insecure-dev-secret-change-me"  # noqa: S105 - シークレットではなく開発用フォールバック定数
_DEV_ENVIRONMENTS = ("development", "test")


def _is_dev_environment() -> bool:
    return get_settings().APP_ENV.lower() in _DEV_ENVIRONMENTS


def _serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    secret = settings.SECRET_ENCRYPTION_KEY
    if not secret:
        if not _is_dev_environment():
            raise RuntimeError(
                "SECRET_ENCRYPTION_KEY が未設定です。development/test 以外の環境では"
                "CSRF署名鍵として必須です(fail-closed)。"
            )
        secret = _DEV_FALLBACK_SECRET
    return URLSafeTimedSerializer(secret, salt=_CSRF_SALT)


def _cookie_secure() -> bool:
    """CSRF CookieのSecure属性。`CSRF_COOKIE_SECURE` 明示設定が最優先、未設定なら自動判定。"""
    override = get_settings().CSRF_COOKIE_SECURE
    if override is not None:
        return override
    return not _is_dev_environment()


def set_csrf_cookie(response: object, token: str) -> None:
    """CSRF Cookieを共通属性(httponly/samesite=strict、非dev環境はsecure)で設定する。"""
    response.set_cookie(  # type: ignore[attr-defined]
        CSRF_COOKIE_NAME,
        token,
        httponly=True,
        samesite="strict",
        secure=_cookie_secure(),
    )


def issue_csrf_token() -> str:
    """署名済みCSRFトークンを発行する(Cookie+hiddenフィールド両方に同じ値を設定する)。"""
    return _serializer().dumps({"v": 1})


def get_or_issue_csrf_token(request: object) -> str:
    """リクエストのCookieに有効なCSRFトークンがあればそれを再利用し、なければ新規発行する。

    ページ表示のたびに新トークンでCookieを上書きすると、複数タブや「戻る」で表示された
    キャッシュ済みページのフォームトークンとCookieが食い違い、正当な操作が
    "CSRF token invalid" になる。トークンをブラウザセッション内で安定させることで防ぐ。
    """
    cookie_value = request.cookies.get(CSRF_COOKIE_NAME)  # type: ignore[attr-defined]
    if cookie_value:
        try:
            _serializer().loads(cookie_value, max_age=CSRF_MAX_AGE_SECONDS)
        except (BadSignature, SignatureExpired):
            pass
        else:
            return str(cookie_value)
    return issue_csrf_token()


def verify_csrf(cookie_value: str | None, form_value: str | None) -> bool:
    """CookieとフォームのCSRFトークンが一致し、かつ署名が有効であることを検証する。"""
    if not cookie_value or not form_value or cookie_value != form_value:
        return False
    try:
        _serializer().loads(cookie_value, max_age=CSRF_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return False
    return True
