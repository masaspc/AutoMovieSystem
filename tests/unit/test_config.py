from __future__ import annotations

import pytest

from app.core.config import Settings


def test_defaults_are_fail_closed() -> None:
    """設定を明示しない場合、公開関連のデフォルトはすべて安全側(fail-closed)であること。"""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.YOUTUBE_DEFAULT_PRIVACY_STATUS == "private"
    assert settings.AUTO_PUBLISH_ENABLED is False
    assert settings.REQUIRE_HUMAN_APPROVAL is True


def test_default_database_is_local_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    # conftestがテスト実行用にDATABASE_URLをin-memory sqliteへ設定するため、
    # 「未設定時のデフォルト値」を検証するにはここで一時的に取り除く必要がある。
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.DATABASE_URL == "sqlite:///./local.db"


def test_default_providers_are_fake() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.LLM_PROVIDER == "fake"
    assert settings.TTS_PROVIDER == "fake"
    assert settings.YOUTUBE_PROVIDER == "fake"
