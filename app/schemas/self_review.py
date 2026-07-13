"""投稿後セルフレビューのLLM構造化出力。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SelfReviewLesson(BaseModel):
    """次回の動画づくりへ反映する改善点1件。"""

    finding: str = Field(min_length=1, max_length=500)
    recommended_action: str = Field(min_length=1, max_length=500)


class SelfReviewReport(BaseModel):
    """動画1本の振り返りレポート。"""

    good_points: list[str] = Field(default_factory=list, max_length=5)
    bad_points: list[str] = Field(default_factory=list, max_length=5)
    lessons: list[SelfReviewLesson] = Field(default_factory=list, max_length=5)
