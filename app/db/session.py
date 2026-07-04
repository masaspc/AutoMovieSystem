"""DBエンジン/セッション(D-013: 同期SQLAlchemy)。"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

_connect_args: dict[str, object] = {}
if settings.DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_engine(settings.DATABASE_URL, connect_args=_connect_args, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def enable_sqlite_foreign_keys(target_engine: Engine) -> None:
    """SQLite接続時に外部キー制約を有効化するリスナーを、指定エンジンにのみ登録する。

    PostgreSQLでは常時有効なため何もしない。テスト用エンジン(in-memory等)にも
    個別に適用できるよう、グローバルな `Engine` クラスではなくインスタンスへ登録する。
    """
    if target_engine.url.get_backend_name() != "sqlite":
        return

    @event.listens_for(target_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


enable_sqlite_foreign_keys(engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI依存: リクエストスコープのDBセッション。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
