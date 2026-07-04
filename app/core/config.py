"""アプリケーション設定。

fail-closed がデフォルト: 非公開(private) / 自動公開オフ / 人間承認必須。
"""

from __future__ import annotations

import shutil
from functools import lru_cache
from glob import glob
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_binary_path(env_value: str, binary_name: str) -> str:
    """FFmpeg/ffprobe の実行ファイルパスを解決する(D-007)。

    優先順位: 明示設定値 -> PATH -> WinGet(Gyan.FFmpeg)の展開先。
    見つからない場合はバイナリ名をそのまま返す(呼び出し側の subprocess が
    PATH 解決を試み、失敗すれば明確なエラーになる)。
    """
    if env_value:
        return env_value

    found = shutil.which(binary_name)
    if found:
        return found

    local_app_data = Path.home() / "AppData" / "Local"
    winget_packages = local_app_data / "Microsoft" / "WinGet" / "Packages"
    pattern = str(winget_packages / "Gyan.FFmpeg*" / "**" / f"{binary_name}.exe")
    matches = glob(pattern, recursive=True)
    if matches:
        return matches[0]

    return binary_name


class Settings(BaseSettings):
    """環境変数から読み込む設定。すべてデフォルトは安全側(fail-closed)。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_ENV: str = "development"

    DATABASE_URL: str = "sqlite:///./local.db"
    REDIS_URL: str = "redis://localhost:6379/0"

    # ローカル検証はデフォルトeager(D-004)。docker compose 環境では .env で false にする。
    CELERY_TASK_ALWAYS_EAGER: bool = True

    # 開発用の空文字列。本番運用では必ず安全な Fernet キーを .env に設定すること。
    SECRET_ENCRYPTION_KEY: str = ""

    # 公開ゲート関連(fail-closed デフォルト)
    YOUTUBE_DEFAULT_PRIVACY_STATUS: str = "private"
    AUTO_PUBLISH_ENABLED: bool = False
    REQUIRE_HUMAN_APPROVAL: bool = True

    # FFmpeg/ffprobe(空ならPATH→WinGet展開先の順に解決)
    FFMPEG_PATH: str = ""
    FFPROBE_PATH: str = ""

    GENERATED_DIR: str = "generated"

    # プロバイダー選択(デフォルトはすべてFake。実APIキー未設定でも全機能デモ可能: D-006)
    LLM_PROVIDER: str = "fake"
    TTS_PROVIDER: str = "fake"
    YOUTUBE_PROVIDER: str = "fake"

    # AI予算(整数マイクロUSD, ADR-0007)。1 USD = 1_000_000 マイクロUSD。
    DAILY_AI_BUDGET_MICRO_USD: int = 5_000_000
    MONTHLY_AI_BUDGET_MICRO_USD: int = 100_000_000

    # Topic スコアリングの重み(仕様§8)。合計は1.0でなければならない
    # (app/services/topics/scoring.py の validate_weights で検証)。
    TOPIC_SCORE_WEIGHT_DEMAND: float = 0.25
    TOPIC_SCORE_WEIGHT_EXPERTISE: float = 0.20
    TOPIC_SCORE_WEIGHT_ORIGINALITY: float = 0.20
    TOPIC_SCORE_WEIGHT_REVENUE: float = 0.15
    TOPIC_SCORE_WEIGHT_FRESHNESS: float = 0.10
    TOPIC_SCORE_WEIGHT_PRODUCTION_COST: float = 0.10

    @property
    def resolved_ffmpeg_path(self) -> str:
        return _resolve_binary_path(self.FFMPEG_PATH, "ffmpeg")

    @property
    def resolved_ffprobe_path(self) -> str:
        return _resolve_binary_path(self.FFPROBE_PATH, "ffprobe")


@lru_cache
def get_settings() -> Settings:
    return Settings()
