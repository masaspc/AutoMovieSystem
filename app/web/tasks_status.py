"""Celeryタスクの進行状況ポーリング用エンドポイント(HTMX)。

長時間かかる処理(台本生成・音声合成・レンダリング・自動レビュー)をCeleryへ
dispatchした後、画面側がこのエンドポイントを2秒間隔でポーリングし、完了したら
`HX-Redirect` で元の画面へ戻す(処理はページを閉じてもワーカー側で継続する)。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.web.common import with_message
from app.workers.celery_app import celery_app

router = APIRouter(tags=["web-tasks"])

_READY_STATES = {"SUCCESS", "FAILURE"}


@router.get("/tasks/{task_id}/status", response_class=HTMLResponse)
def task_status(
    task_id: str,
    request: Request,
    redirect_url: str = "/",
    label: str = "処理",
) -> Response:
    result = celery_app.AsyncResult(task_id)
    if result.state in _READY_STATES:
        target = (
            redirect_url
            if result.state == "SUCCESS"
            else with_message(
                redirect_url, error=f"{label}に失敗しました。詳細は「ジョブ」画面で確認してください"
            )
        )
        response = Response(status_code=200)
        response.headers["HX-Redirect"] = target
        return response

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "_task_banner_fragment.html",
        {"task_id": task_id, "task_label": label, "redirect_url": redirect_url},
    )
