from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

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
