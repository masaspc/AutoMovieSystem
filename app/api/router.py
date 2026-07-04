"""JSON API ルート。Phase1では土台のみ。"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["api"])
