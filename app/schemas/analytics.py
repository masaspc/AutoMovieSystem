"""Phase 6 分析・コメント・Insight APIスキーマ。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class VideoMetricDailyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    publication_id: str
    metric_date: date
    views: int
    watch_minutes: float
    average_view_duration: float
    average_view_percentage: float
    impressions: int
    ctr: float
    likes: int
    comments_count: int
    subscribers_gained: int
    subscribers_lost: int
    estimated_revenue: float
    created_at: datetime
    updated_at: datetime


class CommentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    publication_id: str
    youtube_comment_id: str
    author_hash: str
    text: str
    published_at: datetime
    like_count: int
    category: str
    sentiment: str
    priority: int
    requested_topic: str | None
    requires_response: bool
    moderation_status: str
    created_at: datetime
    updated_at: datetime


class InsightResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_type: str
    source_id: str
    insight_type: str
    source_ref: str
    finding: str
    evidence: dict[str, Any]
    confidence: float
    recommended_action: str
    human_review_reason: str
    created_at: datetime


class FeedbackSyncResponse(BaseModel):
    metric: VideoMetricDailyResponse
    comments_synced: int
    insights: list[InsightResponse]
