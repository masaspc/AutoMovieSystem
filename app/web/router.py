"""管理画面(Jinja2+HTMX)ルート。Phase1では土台のみ。"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["web"])
