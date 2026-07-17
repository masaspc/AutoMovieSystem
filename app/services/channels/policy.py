"""Channel.editorial_policyをfail-softで検証・取得する。"""

from __future__ import annotations

from pydantic import ValidationError

from app.core.logging import get_logger
from app.models.channel import Channel
from app.schemas.editorial_policy import ChannelEditorialPolicy

logger = get_logger(__name__)


def get_editorial_policy(channel: Channel | None) -> ChannelEditorialPolicy:
    """不正・未設定のJSONは警告後に空ポリシーへ戻す。"""
    if channel is None or channel.editorial_policy is None:
        return ChannelEditorialPolicy()
    try:
        return ChannelEditorialPolicy.model_validate(channel.editorial_policy)
    except (TypeError, ValidationError, ValueError) as exc:
        logger.warning(
            "channel_editorial_policy_invalid",
            channel_id=channel.id,
            error_type=type(exc).__name__,
        )
        return ChannelEditorialPolicy()
