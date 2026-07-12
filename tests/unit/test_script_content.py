from app.schemas.script_content import ScriptSection


def test_emphasis_words_are_deduplicated_and_limited_for_llm_output() -> None:
    section = ScriptSection.model_validate(
        {
            "heading": "四則演算",
            "narration": "演算子を説明します。",
            "visual_instruction": "演算子を表示する。",
            "emphasis_words": ["＋", "－", "×", "÷", "＋", "//", "%"],
        }
    )

    assert section.emphasis_words == ["＋", "－", "×", "÷", "//"]


def test_emphasis_words_drop_blank_values() -> None:
    section = ScriptSection.model_validate(
        {
            "heading": "要点",
            "narration": "要点です。",
            "visual_instruction": "要点を表示する。",
            "emphasis_words": ["  ", " 重要 ", ""],
        }
    )

    assert section.emphasis_words == ["重要"]
