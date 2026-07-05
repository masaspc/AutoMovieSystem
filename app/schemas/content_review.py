"""コンテンツレビューLLM構造化出力(仕様§12)。`call_llm` の `response_schema` として使う。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ContentReviewFinding(BaseModel):
    code: str
    severity: str
    message: str
    detail: str | None = None


class ContentReviewResult(BaseModel):
    """タイトル誠実性・誤認表現・スパム性・AI開示要否・子ども向け要否・著作権懸念の構造化レビュー結果。"""

    findings: list[ContentReviewFinding] = Field(default_factory=list)
    passed: bool = True
