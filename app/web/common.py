"""管理画面ルート共通ヘルパー(CSRF検証・フラッシュメッセージ用リダイレクトURL構築)。"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import HTTPException, Request

from app.core.csrf import CSRF_COOKIE_NAME, verify_csrf

OPERATOR_DEFAULT = "admin"


def require_csrf(request: Request, csrf_token: str) -> None:
    """CSRFトークンを検証する。不正なら403を送出する。"""
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not verify_csrf(cookie_token, csrf_token):
        raise HTTPException(status_code=403, detail="CSRF token invalid")


def with_message(url: str, *, error: str | None = None, info: str | None = None) -> str:
    """フラッシュメッセージをクエリパラメータとして付与したリダイレクト先URLを作る。"""
    if error:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}error={quote(error)}"
    if info:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}info={quote(info)}"
    return url
