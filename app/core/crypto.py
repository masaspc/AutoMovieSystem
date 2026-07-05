"""Fernet対称暗号化(docs/architecture.md セキュリティ要点)。

OAuthリフレッシュトークン等のシークレットをDBへ保存する前に必ずこのモジュールで暗号化する。
`SECRET_ENCRYPTION_KEY`(設定)から鍵を導出する。未設定の場合は暗号化・復号操作の
呼び出し時にfail-fastで例外を送出する(平文のままシークレットを保存することを防ぐ)。

復号したシークレット・トークンは呼び出し元でもログへ出力しないこと
(app/core/logging.py のマスキングプロセッサはキー名ベースの防御であり、
ここで復号した生の文字列を直接ログに渡さないことが前提)。
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings, get_settings


class CryptoConfigurationError(RuntimeError):
    """`SECRET_ENCRYPTION_KEY` が未設定のまま暗号化/復号を呼び出した場合。"""


class DecryptionError(RuntimeError):
    """復号に失敗した場合(鍵不一致・改ざん・破損トークン等)。"""


def _derive_fernet_key(secret: str) -> bytes:
    """任意長の設定文字列からFernet用の32byte urlsafe-base64鍵を導出する。"""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _build_fernet(settings: Settings | None = None) -> Fernet:
    settings = settings or get_settings()
    if not settings.SECRET_ENCRYPTION_KEY:
        raise CryptoConfigurationError(
            "SECRET_ENCRYPTION_KEY is not configured. "
            "Refusing to encrypt/decrypt without a configured key (fail-closed)."
        )
    return Fernet(_derive_fernet_key(settings.SECRET_ENCRYPTION_KEY))


def encrypt_secret(plaintext: str, *, settings: Settings | None = None) -> str:
    """平文シークレットをFernetで暗号化し、DB保存可能な文字列トークンを返す。

    Raises:
        CryptoConfigurationError: `SECRET_ENCRYPTION_KEY` が未設定の場合。
    """
    fernet = _build_fernet(settings)
    return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str, *, settings: Settings | None = None) -> str:
    """暗号化トークンを復号し平文シークレットを返す。

    Raises:
        CryptoConfigurationError: `SECRET_ENCRYPTION_KEY` が未設定の場合。
        DecryptionError: トークンが不正、または鍵が一致しない場合。
    """
    fernet = _build_fernet(settings)
    try:
        return fernet.decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise DecryptionError("Failed to decrypt secret: invalid token or wrong key") from exc
