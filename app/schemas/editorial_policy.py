"""チャンネル別の編集方針・安全方針。"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChannelEditorialPolicy(BaseModel):
    """Channel.editorial_policy JSONの検証済み表現。"""

    model_config = ConfigDict(extra="forbid")

    tone: str = Field(default="", max_length=1000)
    target_audience: str = Field(default="", max_length=1000)
    prohibited_instructions: list[str] = Field(default_factory=list, max_length=50)
    disclaimer_text: str = Field(default="", max_length=4000)
    trend_feed_urls: list[str] = Field(default_factory=list, max_length=50)
    default_production_settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tone", "target_audience", "disclaimer_text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("prohibited_instructions")
    @classmethod
    def normalize_instructions(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @field_validator("trend_feed_urls")
    @classmethod
    def validate_feed_urls(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            url = value.strip()
            parsed = urlsplit(url)
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
                raise ValueError("trend_feed_urlsにはhttp(s) URLを指定してください")
            if url not in normalized:
                normalized.append(url)
        return normalized
