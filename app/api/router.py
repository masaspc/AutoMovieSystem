"""JSON API ルート。Phase1では土台のみ。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.topics import router as topics_router

router = APIRouter(prefix="/api", tags=["api"])
router.include_router(topics_router)
