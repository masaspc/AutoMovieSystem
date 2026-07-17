"""Growth quality preflight structured output schemas."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

Score = Annotated[int, Field(ge=0, le=100)]
ShortText = Annotated[str, Field(min_length=1, max_length=500)]
InstructionText = Annotated[str, Field(min_length=1, max_length=1_000)]


class GrowthQualityIssue(BaseModel):
    """One actionable quality finding."""

    code: Annotated[str, Field(min_length=1, max_length=64)]
    severity: Literal["warning", "blocking"]
    message: ShortText


class GrowthQualityReport(BaseModel):
    """Bounded LLM assessment used before expensive media production."""

    appeal_score: Score
    engagement_score: Score
    satisfaction_score: Score
    originality_score: Score
    trust_score: Score
    overall_score: Score
    recommended_title_index: int = Field(ge=0, le=9)
    recommended_thumbnail_index: int = Field(ge=0, le=9)
    strengths: list[ShortText] = Field(default_factory=list, max_length=5)
    issues: list[GrowthQualityIssue] = Field(default_factory=list, max_length=8)
    revision_instructions: list[InstructionText] = Field(default_factory=list, max_length=5)
    human_check_reasons: list[ShortText] = Field(default_factory=list, max_length=5)


class GrowthQualityOutcome(BaseModel):
    """Service result persisted in ``Script.source_manifest``."""

    report: GrowthQualityReport
    quick_approval_ready: bool
    rewrite_performed: bool = False
