"""Topic用Pydanticスキーマ。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TopicCreateRequest(BaseModel):
    channel_id: str
    title: str
    description: str | None = None
    client_key: str = Field(..., description="クライアント生成の一意キー(冪等取り込み用)")


class TopicImportCsvRequest(BaseModel):
    channel_id: str
    csv_text: str


class TopicResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    channel_id: str
    title: str
    description: str | None
    source_type: str
    source_url: str | None
    source_ref: str
    demand_score: float
    revenue_score: float
    originality_score: float
    expertise_score: float
    freshness_score: float
    production_cost_score: float
    risk_level: str
    total_score: float
    status: str
    created_at: datetime
    updated_at: datetime


class TopicImportCsvResponse(BaseModel):
    created: int
    skipped: int
    total_rows: int
