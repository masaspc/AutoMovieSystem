"""管理画面(Jinja2+HTMX)ルート。"""

from __future__ import annotations

from fastapi import APIRouter

from app.web.approvals import router as approvals_router

router = APIRouter(tags=["web"])
router.include_router(approvals_router)
