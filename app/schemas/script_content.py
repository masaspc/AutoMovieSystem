"""台本の構造化データ(仕様§10)。LLM構造化出力の `response_schema` として使う。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CharacterId = Literal["zundamon", "metan", "tsumugi"]
CharacterEmotion = Literal["neutral", "happy", "serious", "surprised"]
VisualType = Literal["dialogue", "code", "key_point", "quiz", "diagram", "steps"]
BackgroundStyle = Literal["classroom", "editor", "card", "quiz", "diagram"]
CharacterLayout = Literal["full", "small_left", "small_right", "hidden"]


class ScriptDialogueLine(BaseModel):
    """立ち絵解説用のセリフ。話者・感情を音声と画面演出に使う。"""

    speaker: CharacterId
    text: str = Field(min_length=1)
    emotion: CharacterEmotion = "neutral"


class ScriptSection(BaseModel):
    heading: str = Field(min_length=1, max_length=500)
    narration: str = Field(min_length=1, max_length=50_000)
    visual_instruction: str = Field(min_length=1, max_length=2_000)
    evidence_ids: list[str] = Field(default_factory=list)
    dialogue: list[ScriptDialogueLine] = Field(default_factory=list)
    visual_type: VisualType = "dialogue"
    background_style: BackgroundStyle = "classroom"
    character_layout: CharacterLayout = "full"
    visual_title: str = Field(default="", max_length=200)
    visual_bullets: list[str] = Field(default_factory=list, max_length=6)
    code: str = Field(default="", max_length=10_000)
    highlight_lines: list[int] = Field(default_factory=list)
    quiz_question: str = Field(default="", max_length=500)
    quiz_options: list[str] = Field(default_factory=list, max_length=4)
    quiz_answer: str = Field(default="", max_length=500)


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
