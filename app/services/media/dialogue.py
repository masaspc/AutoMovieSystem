"""台本の後方互換を保ちながら、掛け合いセリフをメディア工程へ渡す。"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings, get_settings


@dataclass(frozen=True)
class SpeechLine:
    index: int
    section_index: int
    speaker: str
    text: str
    emotion: str
    visual_instruction: str


def dialogue_script_enabled(settings: Settings | None = None) -> bool:
    """掛け合い台本をメディア工程で使う明示的なオプトイン設定。"""
    return (settings or get_settings()).DIALOGUE_SCRIPT_ENABLED


def extract_speech_lines(
    script_body: dict, *, dialogue_enabled: bool | None = None, settings: Settings | None = None
) -> list[SpeechLine]:
    """有効時だけdialogueを優先し、それ以外は後方互換のnarrationを使う。"""
    if dialogue_enabled is None:
        dialogue_enabled = dialogue_script_enabled(settings)

    lines: list[SpeechLine] = []
    for section_index, section in enumerate(script_body.get("sections") or []):
        visual_instruction = str(section.get("visual_instruction") or "")
        dialogue = section.get("dialogue") or []
        if dialogue_enabled and dialogue:
            for item in dialogue:
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                lines.append(
                    SpeechLine(
                        index=len(lines),
                        section_index=section_index,
                        speaker=str(item.get("speaker") or "zundamon"),
                        text=text,
                        emotion=str(item.get("emotion") or "neutral"),
                        visual_instruction=visual_instruction,
                    )
                )
            continue

        narration = str(section.get("narration") or "").strip()
        if narration:
            lines.append(
                SpeechLine(
                    index=len(lines),
                    section_index=section_index,
                    speaker="zundamon",
                    text=narration,
                    emotion="neutral",
                    visual_instruction=visual_instruction,
                )
            )
    return lines
