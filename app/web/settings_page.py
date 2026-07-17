"""環境設定表示とチャンネル編集方針の管理画面。"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.csrf import get_or_issue_csrf_token, set_csrf_cookie
from app.db.session import get_db
from app.models.channel import Channel
from app.schemas.editorial_policy import ChannelEditorialPolicy
from app.services.channels.policy import get_editorial_policy
from app.web.common import require_csrf, with_message

router = APIRouter(tags=["web-settings"])

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbSession = Annotated[Session, Depends(get_db)]


def _configured(value: str) -> str:
    return "設定済み" if value else "未設定"


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, settings: SettingsDep, db: DbSession) -> HTMLResponse:
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

    channel_policies = []
    for channel in db.query(Channel).order_by(Channel.name.asc()).all():
        policy = get_editorial_policy(channel)
        channel_policies.append(
            {
                "channel": channel,
                "policy": policy,
                "default_production_settings_json": json.dumps(
                    policy.default_production_settings, ensure_ascii=False, indent=2
                ),
            }
        )

    csrf_token = get_or_issue_csrf_token(request)
    templates = request.app.state.templates
    response = templates.TemplateResponse(
        request,
        "settings/index.html",
        {
            "general": general,
            "model_mapping": model_mapping,
            "secrets_status": secrets_status,
            "channel_policies": channel_policies,
            "csrf_token": csrf_token,
        },
    )
    set_csrf_cookie(response, csrf_token)
    return response


def _nonempty_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


@router.post("/settings/channels/{channel_id}/editorial-policy")
def update_channel_editorial_policy(
    channel_id: str,
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str, Form()],
    tone: Annotated[str, Form(max_length=1000)] = "",
    target_audience: Annotated[str, Form(max_length=1000)] = "",
    prohibited_instructions: Annotated[str, Form(max_length=10000)] = "",
    disclaimer_text: Annotated[str, Form(max_length=4000)] = "",
    trend_feed_urls: Annotated[str, Form(max_length=10000)] = "",
    default_production_settings_json: Annotated[str, Form(max_length=20000)] = "{}",
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="チャンネルが見つかりません")
    try:
        default_settings = json.loads(default_production_settings_json or "{}")
        if not isinstance(default_settings, dict):
            raise ValueError("既定制作設定はJSONオブジェクトで入力してください")
        policy = ChannelEditorialPolicy(
            tone=tone,
            target_audience=target_audience,
            prohibited_instructions=_nonempty_lines(prohibited_instructions),
            disclaimer_text=disclaimer_text,
            trend_feed_urls=_nonempty_lines(trend_feed_urls),
            default_production_settings=default_settings,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        db.rollback()
        return RedirectResponse(
            url=with_message("/settings", error=f"編集方針を保存できません: {exc}"),
            status_code=303,
        )
    channel.editorial_policy = policy.model_dump(mode="json")
    db.commit()
    return RedirectResponse(
        url=with_message("/settings", info=f"{channel.name}の編集方針を保存しました"),
        status_code=303,
    )
