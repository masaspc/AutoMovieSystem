"""設定(読み取り専用)表示ページ(仕様§16)。シークレットの値そのものは表示しない。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.core.config import Settings, get_settings

router = APIRouter(tags=["web-settings"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


def _configured(value: str) -> str:
    return "設定済み" if value else "未設定"


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, settings: SettingsDep) -> HTMLResponse:
    general = {
        "AUTO_PUBLISH_ENABLED": settings.AUTO_PUBLISH_ENABLED,
        "REQUIRE_HUMAN_APPROVAL": settings.REQUIRE_HUMAN_APPROVAL,
        "YOUTUBE_DEFAULT_PRIVACY_STATUS": settings.YOUTUBE_DEFAULT_PRIVACY_STATUS,
        "LLM_PROVIDER": settings.LLM_PROVIDER,
        "TTS_PROVIDER": settings.TTS_PROVIDER,
        "VOICEVOX_BASE_URL": settings.VOICEVOX_BASE_URL,
        "DIALOGUE_SCRIPT_ENABLED": settings.DIALOGUE_SCRIPT_ENABLED,
        "CHARACTER_RENDER_ENABLED": settings.CHARACTER_RENDER_ENABLED,
        "CHARACTER_ASSETS_DIR": settings.CHARACTER_ASSETS_DIR,
        "DIALOGUE_CAST": settings.DIALOGUE_CAST,
        "YOUTUBE_PROVIDER": settings.YOUTUBE_PROVIDER,
        "DAILY_AI_BUDGET_MICRO_USD": settings.DAILY_AI_BUDGET_MICRO_USD,
        "MONTHLY_AI_BUDGET_MICRO_USD": settings.MONTHLY_AI_BUDGET_MICRO_USD,
    }
    model_mapping = {
        "low": settings.LLM_MODEL_LOW,
        "mid": settings.LLM_MODEL_MID,
        "high": settings.LLM_MODEL_HIGH,
    }
    secrets_status = {
        "SECRET_ENCRYPTION_KEY": _configured(settings.SECRET_ENCRYPTION_KEY),
        "ANTHROPIC_API_KEY": _configured(settings.ANTHROPIC_API_KEY),
        "YOUTUBE_OAUTH_CLIENT_ID": _configured(settings.YOUTUBE_OAUTH_CLIENT_ID),
        "YOUTUBE_OAUTH_CLIENT_SECRET": _configured(settings.YOUTUBE_OAUTH_CLIENT_SECRET),
    }

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "settings/index.html",
        {"general": general, "model_mapping": model_mapping, "secrets_status": secrets_status},
    )
