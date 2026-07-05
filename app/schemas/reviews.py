"""Review用Pydanticスキーマ(API応答)。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ReviewFindingResponse(BaseModel):
    code: str
    severity: str
    message: str
    detail: str | None = None


class ReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    video_project_id: str
    reviewer_type: str
    review_version: int
    score: float | None
    passed: bool
    findings: list[dict]
    blocking_findings: list[dict]
    created_at: datetime
