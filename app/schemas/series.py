"""シリーズカリキュラムのLLM構造化出力。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CurriculumEpisode(BaseModel):
    position: int = Field(ge=1, le=100)
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1, max_length=5_000)
    learning_objectives: list[str] = Field(min_length=1, max_length=10)
    prerequisite_positions: list[int] = Field(default_factory=list)
    new_concepts: list[str] = Field(default_factory=list, max_length=10)
    review_concepts: list[str] = Field(default_factory=list, max_length=10)
    excluded_concepts: list[str] = Field(default_factory=list, max_length=20)
    demo_outline: str = Field(default="", max_length=5_000)
    exercise_outline: str = Field(default="", max_length=5_000)
    next_episode_bridge: str = Field(default="", max_length=2_000)
    target_duration_seconds: int = Field(default=300, ge=30, le=3600)


class CurriculumPlan(BaseModel):
    episodes: list[CurriculumEpisode] = Field(min_length=1, max_length=100)
