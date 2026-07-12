"""エンディング(チャンネル登録CTA)自動付与の検証。"""

from __future__ import annotations

from app.schemas.script_content import ScriptContent, ScriptSection
from app.services.scripts.outro import (
    OUTRO_VISUAL_TYPE,
    append_outro_section,
    build_outro_section,
)


def _content() -> ScriptContent:
    return ScriptContent(
        title_candidates=["タイトル"],
        target_audience="初心者",
        viewer_problem="課題",
        promised_outcome="成果",
        hook="フック",
        sections=[
            ScriptSection(heading="本編", narration="本編です。", visual_instruction="背景")
        ],
        conclusion="まとめ",
        call_to_action="行動喚起",
        description="説明",
        tags=["tag"],
        chapters=["0:00 本編"],
    )


def test_outro_is_appended_with_subscribe_cta_dialogue() -> None:
    content = append_outro_section(
        _content(), seed="topic-1", cast=["zundamon", "metan", "tsumugi"]
    )

    outro = content.sections[-1]
    assert outro.visual_type == OUTRO_VISUAL_TYPE
    # ずんだもん×つむぎの掛け合いで、グッドボタンとチャンネル登録の呼びかけを必ず含む。
    speakers = [line.speaker for line in outro.dialogue]
    assert speakers[0] == "zundamon"
    assert "tsumugi" in speakers
    combined = "".join(line.text for line in outro.dialogue)
    assert "グッドボタン" in combined
    assert "チャンネル登録" in combined
    # dialogue無効環境向けのnarrationにも同じ内容が入る。
    assert "チャンネル登録" in outro.narration


def test_outro_falls_back_to_metan_when_tsumugi_not_in_cast() -> None:
    content = append_outro_section(_content(), seed="topic-1", cast=["zundamon", "metan"])
    speakers = [line.speaker for line in content.sections[-1].dialogue]
    assert "tsumugi" not in speakers
    assert "metan" in speakers


def test_outro_append_is_idempotent() -> None:
    content = append_outro_section(_content(), seed="topic-1", cast=["zundamon", "metan"])
    section_count = len(content.sections)
    content = append_outro_section(content, seed="topic-1", cast=["zundamon", "metan"])
    assert len(content.sections) == section_count


def test_outro_variation_is_deterministic_per_seed() -> None:
    first = build_outro_section(seed="topic-1", cast=["zundamon", "metan"])
    second = build_outro_section(seed="topic-1", cast=["zundamon", "metan"])
    assert first.dialogue[0].text == second.dialogue[0].text
