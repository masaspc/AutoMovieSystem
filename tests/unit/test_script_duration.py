"""`app.services.scripts.duration` の単体テスト(Phase 2)。"""

from __future__ import annotations

from app.schemas.production_settings import ProductionSettings
from app.schemas.script_content import ScriptContent
from app.services.scripts.duration import (
    CHARS_PER_MINUTE,
    count_script_characters,
    duration_within_range,
    estimate_duration_seconds,
)


def _make_content(*, with_dialogue: bool) -> ScriptContent:
    return ScriptContent.model_validate(
        {
            "title_candidates": ["タイトル案"],
            "target_audience": "初心者",
            "viewer_problem": "課題",
            "promised_outcome": "成果",
            "hook": "12345",  # 5文字
            "sections": [
                {
                    "heading": "導入",
                    "narration": "1234567890",  # 10文字
                    "visual_instruction": "背景を表示する。",
                    "evidence_ids": [],
                    "dialogue": [
                        {"speaker": "zundamon", "text": "12345", "emotion": "neutral"},  # 5文字
                        {"speaker": "metan", "text": "1234567890", "emotion": "neutral"},  # 10文字
                    ],
                },
                {
                    "heading": "本編",
                    "narration": "12345",  # 5文字
                    "visual_instruction": "資料を表示する。",
                    "evidence_ids": [],
                    "dialogue": [],
                },
            ],
            "conclusion": "1234567890",  # 10文字
            "call_to_action": "12345",  # 5文字
            "description": "説明",
            "tags": ["tag"],
            "chapters": ["導入", "本編"],
        }
    )


def test_count_script_characters_uses_narration_when_dialogue_disabled() -> None:
    content = _make_content(with_dialogue=True)
    # 実際に音声化されるsection narrationのみ(10+5)。
    assert count_script_characters(content, dialogue_enabled=False) == 15


def test_count_script_characters_uses_dialogue_when_enabled() -> None:
    content = _make_content(with_dialogue=True)
    # section1のdialogue(5+10=15) + section2はdialogue空なのでnarration(5) = 20
    assert count_script_characters(content, dialogue_enabled=True) == 20


def test_estimate_duration_seconds_uses_chars_per_minute_and_speaking_rate() -> None:
    content = _make_content(with_dialogue=True)
    settings = ProductionSettings(speaking_rate=1.0)
    characters = count_script_characters(content, dialogue_enabled=False)
    expected = characters / (CHARS_PER_MINUTE * 1.0) * 60
    assert estimate_duration_seconds(content, settings, dialogue_enabled=False) == expected


def test_estimate_duration_seconds_scales_with_speaking_rate() -> None:
    content = _make_content(with_dialogue=True)
    normal = ProductionSettings(speaking_rate=1.0)
    fast = ProductionSettings(speaking_rate=2.0)
    normal_estimate = estimate_duration_seconds(content, normal, dialogue_enabled=False)
    fast_estimate = estimate_duration_seconds(content, fast, dialogue_enabled=False)
    # 話速を2倍にすると推定尺は半分になる。
    assert fast_estimate == normal_estimate / 2


def test_duration_within_range_boundaries_are_inclusive() -> None:
    settings = ProductionSettings(
        target_duration_seconds=45, min_duration_seconds=30, max_duration_seconds=60
    )
    assert duration_within_range(30.0, settings) is True
    assert duration_within_range(60.0, settings) is True
    assert duration_within_range(29.999, settings) is False
    assert duration_within_range(60.001, settings) is False
