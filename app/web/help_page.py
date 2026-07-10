"""使い方ガイドページ(初めて触る人向けの操作説明)。DBアクセス不要の静的コンテンツ。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["web-help"])


@router.get("/help", response_class=HTMLResponse)
def help_guide(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "help/index.html", {})
