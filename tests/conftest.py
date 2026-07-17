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
# 同様に LLM_PROVIDER 等が開発者ローカルの .env(local/anthropic等)に設定されていても、
# ユニットテストが実API/実ローカルLLMへ接続しないよう常に fake へ固定する
# (絶対原則: テストで実APIを呼ばない)。
os.environ["LLM_PROVIDER"] = "fake"
# 個別ポリシーは未設定(空文字)にし、LLM_PROVIDER=fake へフォールバックさせる
# (.env.example のデフォルトと同じ挙動。空文字なら LLM_PROVIDER に従う: D-020)。
os.environ["LLM_PROVIDER_LOW"] = ""
os.environ["LLM_PROVIDER_MID"] = ""
os.environ["LLM_PROVIDER_HIGH"] = ""
os.environ["TTS_PROVIDER"] = "fake"
os.environ["YOUTUBE_PROVIDER"] = "fake"
# トレンド一覧もローカル.envのrss設定を読ませず、決定的Fakeへ固定する。
os.environ["TREND_PROVIDER"] = "fake"
os.environ["TREND_FEED_URLS"] = ""
# 自動運転は明示オプトイン。開発者ローカル.envで有効でも通常テストが候補収集や
# Celery dispatchを始めないよう、安全側の既定値へ固定する。
os.environ["GROWTH_AUTOPILOT_ENABLED"] = "false"
os.environ["GROWTH_AUTOPILOT_CHANNEL_ID"] = ""
os.environ["GROWTH_AUTOPILOT_DAILY_LIMIT"] = "1"
# DIALOGUE_SCRIPT_ENABLED/CHARACTER_RENDER_ENABLED も同様。開発者ローカルの .env で
# trueにしていると、ダミー画像・音声を使う既存テストがPillowでの読み込み等に失敗する。
# 個別にこの機能を検証するテストは Settings(...) 直接構築か monkeypatch.setenv で
# 明示的に有効化しており、この既定値(.env.example相当)を上書きするため影響しない。
os.environ["DIALOGUE_SCRIPT_ENABLED"] = "false"
os.environ["CHARACTER_RENDER_ENABLED"] = "false"

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
def ffmpeg_required() -> None:
    """実FFmpeg/ffprobeを要するテスト用のガード。

    未検出時はskipするが、CIのメディアジョブ等で `AMX_REQUIRE_FFMPEG=1` が設定されて
    いる場合は「skipで隠さず」失敗させる(FFmpeg必須環境でのインストール漏れ検知)。
    """
    from app.services.media.tools import check_media_tools

    status = check_media_tools()
    if status.ok:
        return
    message = (
        f"ffmpeg/ffprobeが見つかりません "
        f"(ffmpeg={status.ffmpeg_path}: {'ok' if status.ffmpeg_available else 'missing'}, "
        f"ffprobe={status.ffprobe_path}: {'ok' if status.ffprobe_available else 'missing'})"
    )
    if os.environ.get("AMX_REQUIRE_FFMPEG", "").lower() in ("1", "true"):
        pytest.fail(f"AMX_REQUIRE_FFMPEG=1 が設定されていますが、{message}")
    pytest.skip(message)


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
