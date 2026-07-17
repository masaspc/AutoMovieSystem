"""Channel.editorial_policyをfail-softで検証・取得する。"""

from __future__ import annotations

import hashlib

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


def editorial_policy_checksum(policy: ChannelEditorialPolicy) -> str:
    """台本生成の冪等キーへ含める正規化済みポリシーハッシュ。"""
    return hashlib.sha256(policy.model_dump_json().encode("utf-8")).hexdigest()


def editorial_policy_prompt_block(policy: ChannelEditorialPolicy) -> str:
    """空ポリシー以外を台本生成の必須指示へ変換する。"""
    lines: list[str] = []
    if policy.tone:
        lines.append(f"- トーン: {policy.tone}")
    if policy.target_audience:
        lines.append(f"- 対象視聴者: {policy.target_audience}")
    if policy.prohibited_instructions:
        lines.append("- 禁止事項:")
        lines.extend(f"  - {instruction}" for instruction in policy.prohibited_instructions)
    if not lines:
        return ""
    return "\n\n【チャンネル編集方針(必ず優先して反映)】\n" + "\n".join(lines)
