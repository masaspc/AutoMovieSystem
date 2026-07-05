"""Publication用Pydanticスキーマ(API応答/リクエスト)。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PublicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    video_project_id: str
    youtube_video_id: str | None
    title: str
    description: str
    tags: list[str]
    privacy_status: str
    scheduled_at: datetime | None
    published_at: datetime | None
    upload_status: str
    last_error: str | None
    created_at: datetime


class ScheduleRequest(BaseModel):
    publish_at: datetime


class ScheduleResponse(BaseModel):
    scheduled: bool
    reasons: list[str]
    publication: PublicationResponse | None
