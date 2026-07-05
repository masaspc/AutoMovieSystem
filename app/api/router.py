"""JSON API ルート。Phase1では土台のみ。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.analytics import router as analytics_router
from app.api.publications import router as publications_router
from app.api.reviews import router as reviews_router
from app.api.scripts import router as scripts_router
from app.api.topics import router as topics_router

router = APIRouter(prefix="/api", tags=["api"])
router.include_router(analytics_router)
router.include_router(topics_router)
router.include_router(scripts_router)
router.include_router(reviews_router)
router.include_router(publications_router)
