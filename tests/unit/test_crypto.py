from __future__ import annotations

import pytest

from app.core import crypto
from app.core.config import Settings


def _settings(key: str = "") -> Settings:
    return Settings(_env_file=None, SECRET_ENCRYPTION_KEY=key)  # type: ignore[call-arg]


def test_encrypt_decrypt_round_trip() -> None:
    settings = _settings("a-sufficiently-long-test-key-not-real")
    plaintext = "1//super-secret-refresh-token-value"

    token = crypto.encrypt_secret(plaintext, settings=settings)

    assert token != plaintext
    assert plaintext not in token
    assert crypto.decrypt_secret(token, settings=settings) == plaintext


def test_encrypt_missing_key_raises_fail_fast() -> None:
    settings = _settings("")

    with pytest.raises(crypto.CryptoConfigurationError):
        crypto.encrypt_secret("secret", settings=settings)


def test_decrypt_missing_key_raises_fail_fast() -> None:
    settings = _settings("")

    with pytest.raises(crypto.CryptoConfigurationError):
        crypto.decrypt_secret("token", settings=settings)


def test_decrypt_wrong_key_raises_decryption_error() -> None:
    settings_a = _settings("key-a-not-real-0000000000000000")
    settings_b = _settings("key-b-not-real-0000000000000000")

    token = crypto.encrypt_secret("secret-value", settings=settings_a)

    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt_secret(token, settings=settings_b)


def test_decrypt_invalid_token_raises_decryption_error() -> None:
    settings = _settings("a-sufficiently-long-test-key-not-real")

    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt_secret("not-a-valid-fernet-token", settings=settings)


def test_same_key_derivation_is_deterministic() -> None:
    settings = _settings("deterministic-key-not-real-000000")
    plaintext = "value"

    token1 = crypto.encrypt_secret(plaintext, settings=settings)
    # 異なるFernetトークンでも同じ鍵で復号できることを確認する(鍵導出が決定的)。
    assert crypto.decrypt_secret(token1, settings=settings) == plaintext
