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
from app.core.logging import configure_logging, get_logger
from app.db.session import get_db
from app.web.router import router as web_router

APP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

logger = get_logger(__name__)


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(title="Auto Movie System")

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

    # 管理画面/API全体にHTTP Basic認証を適用する(D-019)。/health は除外(監視用)。
    admin_dependency = [Depends(require_admin)]
    app.include_router(web_router, dependencies=admin_dependency)
    app.include_router(api_router, dependencies=admin_dependency)

    @app.get("/health")
    def health(db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}

    logger.info("app_created")
    return app


app = create_app()
