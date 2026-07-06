"""管理画面/API全体のHTTP Basic認証(D-019)。fail-closedがデフォルト。

`ADMIN_PASSWORD` が設定されている場合は `secrets.compare_digest` でタイミング攻撃
耐性のある比較を行う。未設定の場合は `APP_ENV` が development/test のときのみ
認証をスキップする(それ以外の環境ではパスワード未設定でも常に401)。
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_security = HTTPBasic(auto_error=False)

# ADMIN_PASSWORD未設定時に認証スキップを許可するAPP_ENV値。
_DEV_BYPASS_APP_ENVS = ("development", "test")

_DEV_ANONYMOUS_USERNAME = "dev-anonymous"

# プロセス起動後、開発用バイパス警告は1回だけログ出力する。
_dev_bypass_warned = False


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized",
        headers={"WWW-Authenticate": "Basic"},
    )


def require_admin(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_security)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """管理画面/API全体に適用するHTTP Basic認証dependency。認証済みユーザー名を返す。"""
    global _dev_bypass_warned

    if not settings.ADMIN_PASSWORD:
        if settings.APP_ENV in _DEV_BYPASS_APP_ENVS:
            if not _dev_bypass_warned:
                logger.warning(
                    "admin_auth_bypassed_dev_mode",
                    app_env=settings.APP_ENV,
                )
                _dev_bypass_warned = True
            return _DEV_ANONYMOUS_USERNAME
        # fail-closed: 本番相当の環境でADMIN_PASSWORD未設定なら常に拒否する。
        raise _unauthorized()

    if credentials is None:
        raise _unauthorized()

    username_ok = secrets.compare_digest(credentials.username, settings.ADMIN_USERNAME)
    password_ok = secrets.compare_digest(credentials.password, settings.ADMIN_PASSWORD)
    if not (username_ok and password_ok):
        raise _unauthorized()

    return credentials.username
