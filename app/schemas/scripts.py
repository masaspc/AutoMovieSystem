"""Script用Pydanticスキーマ(API応答)。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FindingResponse(BaseModel):
    code: str
    severity: str
    message: str


class ScriptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    topic_id: str
    version: int
    title: str
    hook: str | None
    body: dict
    conclusion: str | None
    call_to_action: str | None
    source_manifest: dict
    model_name: str | None
    prompt_version: str | None
    input_tokens: int
    output_tokens: int
    estimated_cost_micro_usd: int
    status: str
    created_at: datetime
    updated_at: datetime
    findings: list[FindingResponse] = []
