"""アプリケーション設定。

fail-closed がデフォルト: 非公開(private) / 自動公開オフ / 人間承認必須。
"""

from __future__ import annotations

import shutil
from functools import lru_cache
from glob import glob
from pathlib import Path

from pydantic import field_validator
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

    # 管理画面/API全体のHTTP Basic認証(D-019)。ADMIN_PASSWORD未設定時は
    # APP_ENV が development/test の場合のみ認証をスキップする(それ以外はfail-closedで401)。
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = ""

    # CSRF CookieのSecure属性。未設定(None)なら自動: development/test以外でSecure付与。
    # TLS終端なしのLAN(http://<IP>:8000)でproduction運用する場合のみ false を明示する
    # (ブラウザはlocalhost以外の平文HTTPでSecure Cookieを保存しないため)。
    CSRF_COOKIE_SECURE: bool | None = None

    @field_validator("CSRF_COOKIE_SECURE", mode="before")
    @classmethod
    def _empty_csrf_cookie_secure_as_none(cls, value: object) -> object:
        # .env の `CSRF_COOKIE_SECURE=`(空文字)を「未設定=自動判定」として扱う。
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

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

    # ジョブのlease期間(秒。D-016)。JobRun.status=="started" のまま
    # `started_at + この秒数` を超えていなければ「他プロセスが実行中」とみなし、
    # 再実行せず in_progress を返す(並行実行防止)。超過していればクラッシュ残骸(stale)
    # とみなし、従来どおり attempt をインクリメントして再実行する。
    JOB_LEASE_TIMEOUT_SECONDS: int = 3600

    # FFmpeg/ffprobe呼び出しのタイムアウト(秒)。
    MEDIA_FFMPEG_TIMEOUT_SECONDS: float = 300.0
    MEDIA_FFPROBE_TIMEOUT_SECONDS: float = 30.0

    # 字幕の動画への焼き込み。デフォルトOFF(YouTubeの自動字幕/アップロード字幕に委ねる。
    # 焼き込みは画面からはみ出す・消せない等のデメリットがあるため明示オプトイン)。
    # OFFでもSRT/VTTファイル自体はAssetとして生成される。
    SUBTITLE_BURN_IN_ENABLED: bool = False

    # BGM/SE素材ディレクトリ(assets/bgm/README.md参照。素材が無ければ無音で完走する)。
    BGM_ASSETS_DIR: str = "assets/bgm"
    SE_ASSETS_DIR: str = "assets/se"

    # プロバイダー選択(デフォルトはすべてFake。実APIキー未設定でも全機能デモ可能: D-006)
    LLM_PROVIDER: str = "fake"
    TTS_PROVIDER: str = "fake"
    YOUTUBE_PROVIDER: str = "fake"

    # 実YouTubeプロバイダー用のOAuthクライアント情報(installed app flow:
    # scripts/youtube_oauth_setup.py)。未設定でもFakeで全機能デモ可能(D-006)。
    # リフレッシュトークンはOAuthTokenへ暗号化保存。
    YOUTUBE_OAUTH_CLIENT_ID: str = ""
    YOUTUBE_OAUTH_CLIENT_SECRET: str = ""
    # 動画アップロードのresumable upload チャンクサイズ(バイト。256KiBの倍数)。
    YOUTUBE_UPLOAD_CHUNK_SIZE_BYTES: int = 4 * 1024 * 1024

    # TTS_PROVIDER=voicevox 時のVOICEVOX Engine設定。speaker idの既定値はノーマル声。
    VOICEVOX_BASE_URL: str = "http://127.0.0.1:50021"
    VOICEVOX_TIMEOUT_SECONDS: float = 120.0
    VOICEVOX_SPEAKER_ZUNDAMON: int = 3
    VOICEVOX_SPEAKER_METAN: int = 2
    VOICEVOX_SPEAKER_TSUMUGI: int = 8

    # TTS_PROVIDER=generic_command 時のコマンドテンプレート(引数配列。JSON文字列で指定)。
    # 例: '["voicevox_cli", "--text", "{text}", "--voice", "{voice}", "--out", "{output}"]'
    TTS_GENERIC_COMMAND_TEMPLATE: str = ""
    TTS_GENERIC_COMMAND_TIMEOUT_SECONDS: float = 60.0

    # 掛け合い台本・話者別VOICEVOXを明示的に使う場合だけtrueにする。既定は既存TTS互換を優先。
    DIALOGUE_SCRIPT_ENABLED: bool = False
    # 立ち絵解説動画。利用許諾を確認した公式素材を CHARACTER_ASSETS_DIR に配置して有効化する。
    CHARACTER_RENDER_ENABLED: bool = False
    CHARACTER_ASSETS_DIR: str = "assets/characters"
    # 掛け合い台本で使用を許可する話者。つむぎを外せば2人掛け合いだけで量産できる。
    DIALOGUE_CAST: str = "zundamon,metan,tsumugi"

    # Anthropic LLMプロバイダー設定。APIキー未設定でもFakeで全機能デモ可能(D-006)。
    ANTHROPIC_API_KEY: str = ""
    # model_policy ("low"|"mid"|"high") -> 実際のモデルID。
    LLM_MODEL_LOW: str = "claude-haiku-4-5-20251001"
    LLM_MODEL_MID: str = "claude-sonnet-5"
    LLM_MODEL_HIGH: str = "claude-opus-4-8"

    # ローカルLLM(Ollama/LM Studio/vLLM等のOpenAI互換 Chat Completions API: D-020)。
    # base_url例: Ollama="http://localhost:11434/v1", LM Studio="http://localhost:1234/v1"
    LOCAL_LLM_BASE_URL: str = "http://localhost:11434/v1"
    # 空ならAuthorizationヘッダーなし(Ollama等はキー不要)。設定時は "Bearer {key}"。
    LOCAL_LLM_API_KEY: str = ""
    # model_policy ("low"|"mid"|"high") -> ローカルモデル名。
    LOCAL_LLM_MODEL_LOW: str = "qwen3:8b"
    LOCAL_LLM_MODEL_MID: str = "qwen3:32b"
    LOCAL_LLM_MODEL_HIGH: str = "qwen3:32b"
    # ローカル推論は低速なため長めのデフォルト。
    LOCAL_LLM_TIMEOUT_SECONDS: float = 300.0

    # model_policy別のプロバイダー選択(D-020)。空文字なら LLM_PROVIDER に従う。
    # 有効値: "fake" | "anthropic" | "local"。
    LLM_PROVIDER_LOW: str = ""
    LLM_PROVIDER_MID: str = ""
    LLM_PROVIDER_HIGH: str = ""

    # AI予算(整数マイクロUSD, ADR-0007)。1 USD = 1_000_000 マイクロUSD。
    DAILY_AI_BUDGET_MICRO_USD: int = 5_000_000
    MONTHLY_AI_BUDGET_MICRO_USD: int = 100_000_000

    # グロース目標: 登録者数の到達目標(成長ダッシュボードの進捗計算に使用)。
    GROWTH_SUBSCRIBER_TARGET: int = 1000

    # Topic スコアリングの重み(仕様§8)。合計は1.0でなければならない
    # (app/services/topics/scoring.py の validate_weights で検証)。
    TOPIC_SCORE_WEIGHT_DEMAND: float = 0.25
    TOPIC_SCORE_WEIGHT_EXPERTISE: float = 0.20
    TOPIC_SCORE_WEIGHT_ORIGINALITY: float = 0.20
    TOPIC_SCORE_WEIGHT_REVENUE: float = 0.15
    TOPIC_SCORE_WEIGHT_FRESHNESS: float = 0.10
    TOPIC_SCORE_WEIGHT_PRODUCTION_COST: float = 0.10

    # 機械検査(仕様§12・app/services/reviews/machine.py)の許容範囲。
    REVIEW_DURATION_MIN_RATIO: float = 0.85
    REVIEW_DURATION_MAX_RATIO: float = 1.15
    REVIEW_MIN_WIDTH: int = 1920
    REVIEW_MIN_HEIGHT: int = 1080
    REVIEW_SILENCE_THRESHOLD_DB: float = -50.0
    REVIEW_SILENCE_MIN_DURATION_SECONDS: float = 10.0
    REVIEW_VOLUME_MIN_DB: float = -30.0
    REVIEW_VOLUME_MAX_DB: float = -5.0

    # 公開可否ゲート(app/services/reviews/gate.py)の高リスク領域キーワード(カンマ区切り)。
    REVIEW_HIGH_RISK_KEYWORDS: str = "投資,医療,法律,セキュリティ"

    @property
    def resolved_ffmpeg_path(self) -> str:
        return _resolve_binary_path(self.FFMPEG_PATH, "ffmpeg")

    @property
    def resolved_ffprobe_path(self) -> str:
        return _resolve_binary_path(self.FFPROBE_PATH, "ffprobe")

    @property
    def resolved_high_risk_keywords(self) -> tuple[str, ...]:
        return tuple(k.strip() for k in self.REVIEW_HIGH_RISK_KEYWORDS.split(",") if k.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
