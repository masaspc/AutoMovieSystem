"""FastAPIアプリファクトリ。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.router import router as api_router
from app.core.auth import require_admin
from app.core.config import Settings
from app.core.logging import configure_logging, get_logger
from app.db.session import get_db
from app.services.media.tools import check_media_tools
from app.web.router import router as web_router

APP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

logger = get_logger(__name__)


def create_app() -> FastAPI:
    configure_logging()
    # 起動時の公開ドキュメント設定だけは、リクエスト側の設定キャッシュを汚さずに読む。
    settings = Settings()
    is_development = settings.APP_ENV in ("development", "test")

    app = FastAPI(
        title="Auto Movie System",
        docs_url="/docs" if is_development else None,
        redoc_url="/redoc" if is_development else None,
        openapi_url="/openapi.json" if is_development else None,
    )

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    # CSS更新がブラウザのヒューリスティックキャッシュで反映されない問題への対策:
    # 内容ハッシュをクエリ文字列に付与してキャッシュバストする(テンプレートから参照)。
    admin_css = STATIC_DIR / "admin.css"
    asset_version = (
        hashlib.sha256(admin_css.read_bytes()).hexdigest()[:12] if admin_css.exists() else "0"
    )
    templates.env.globals["asset_version"] = asset_version
    app.state.templates = templates

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.middleware("http")
    async def no_store_html(request, call_next):  # type: ignore[no-untyped-def]
        # 認証済み管理画面HTMLのブラウザキャッシュを禁止する。「戻る」で古いページが
        # 表示されCSRFトークンや状態表示が食い違う問題を防ぐ(静的ファイル・動画は対象外)。
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # 管理画面/API全体にHTTP Basic認証を適用する(D-019)。/health は除外(監視用)。
    admin_dependency = [Depends(require_admin)]
    app.include_router(web_router, dependencies=admin_dependency)
    app.include_router(api_router, dependencies=admin_dependency)

    @app.get("/health")
    def health(db: Annotated[Session, Depends(get_db)]) -> dict[str, str | bool]:
        # FFmpeg未検出でも管理画面・API自体は利用可能なため、HTTP 200を維持しつつ
        # DB状態とレンダリング可否を分けて返す(Docker等のヘルスチェックはHTTP 200のみ
        # を見ること。動画生成の可否は ready_for_rendering を参照)。
        db.execute(text("SELECT 1"))
        tools = check_media_tools()
        return {
            "status": "ok" if tools.ok else "degraded",
            "database": "ok",
            "ffmpeg": "ok" if tools.ffmpeg_available else "missing",
            "ffprobe": "ok" if tools.ffprobe_available else "missing",
            "ready_for_rendering": tools.ok,
        }

    # FFmpeg/ffprobe 未検出はレンダリング実行時まで顕在化しないため、起動時に
    # `-version` の実行まで確認して警告を出す(アプリ自体は起動を継続する。
    # レンダリング以外の機能は FFmpeg なしでも動くため)。
    startup_tools = check_media_tools(settings, verify_execution=True)
    if not startup_tools.ok:
        logger.warning(
            "media_tools_missing",
            ffmpeg_path=startup_tools.ffmpeg_path,
            ffmpeg_available=startup_tools.ffmpeg_available,
            ffprobe_path=startup_tools.ffprobe_path,
            ffprobe_available=startup_tools.ffprobe_available,
        )

    logger.info("app_created")
    return app


app = create_app()
