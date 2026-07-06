from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
# HTTP Basic認証(app.core.auth)はADMIN_PASSWORD未設定時、APP_ENVがdevelopment/testの
# 場合のみスキップする。既存テストへの影響を避けるため明示的にtestへ固定する。
os.environ.setdefault("APP_ENV", "test")
# 開発者ローカルの .env(compose用の ADMIN_PASSWORD / CELERY_TASK_ALWAYS_EAGER=false 等)が
# pydantic-settings 経由でテストへ漏れないよう、環境変数として明示上書きする
# (環境変数は .env より優先される)。CIが設定した値は個別テスト側の想定と一致させる。
os.environ["ADMIN_PASSWORD"] = ""
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"

import app.models  # noqa: F401  metadataにモデルを登録するため import
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import enable_sqlite_foreign_keys, get_db
from app.main import create_app


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Generator[None, None, None]:
    """テスト間で `get_settings()` のlru_cacheを共有しないようにする。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """SQLite in-memory エンジン + metadata.create_all のセッションフィクスチャ。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)

    testing_session_local = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True
    )
    session = testing_session_local()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """DB依存を上書きしたTestClient。"""
    app = create_app()

    def _get_db_override() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
