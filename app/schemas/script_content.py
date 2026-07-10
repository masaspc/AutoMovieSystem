"""台本の構造化データ(仕様§10)。LLM構造化出力の `response_schema` として使う。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CharacterId = Literal["zundamon", "metan", "tsumugi"]
CharacterEmotion = Literal["neutral", "happy", "serious", "surprised"]


class ScriptDialogueLine(BaseModel):
    """立ち絵解説用のセリフ。話者・感情を音声と画面演出に使う。"""

    speaker: CharacterId
    text: str = Field(min_length=1)
    emotion: CharacterEmotion = "neutral"


class ScriptSection(BaseModel):
    heading: str
    narration: str
    visual_instruction: str
    evidence_ids: list[str] = Field(default_factory=list)
    dialogue: list[ScriptDialogueLine] = Field(default_factory=list)


class ScriptContent(BaseModel):
    """台本1本分の構造化データ(仕様§10)。`Script.body` にそのままJSONで保存する。"""

    title_candidates: list[str]
    target_audience: str
    viewer_problem: str
    promised_outcome: str
    hook: str
    sections: list[ScriptSection]
    conclusion: str
    call_to_action: str
    description: str
    tags: list[str]
    chapters: list[str]
