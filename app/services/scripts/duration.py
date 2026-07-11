"""台本の文字数から推定尺を計算する(Phase 2)。

台本生成直後の尺検査・修復ループ(`app/services/scripts/generator.py`)で使う。
TTS合成後の実測尺(`VideoProject.target_duration_seconds`)が正であり、ここでの
推定値はあくまでLLM生成直後のセルフチェック用。
"""

from __future__ import annotations

from app.schemas.production_settings import ProductionSettings
from app.schemas.script_content import ScriptContent

# 日本語ナレーション280〜330文字/分の中央値。TTS合成後の実測尺を正とする
# (この定数はLLM生成直後の推定にのみ使う)。
CHARS_PER_MINUTE = 300


def count_script_characters(content: ScriptContent, *, dialogue_enabled: bool) -> int:
    """台本の音声化対象文字数を合計する。

    `dialogue_enabled` の場合は各sectionのdialogue全行のtextを、そうでなければ
    narrationを数える(`app/services/media/dialogue.py` の音声化ロジックと対応)。
    """
    # 現行メディア工程(app/services/media/dialogue.py)が音声化するのはsectionsのみ。
    # hook/conclusion/call_to_actionはScriptのメタ情報として保存されるが音声トラックには
    # 入らないため、推定へ加えると実測尺を恒常的に過大評価してしまう。
    total = 0
    for section in content.sections:
        if dialogue_enabled and section.dialogue:
            total += sum(len(line.text) for line in section.dialogue)
        else:
            total += len(section.narration)
    return total


def estimate_duration_seconds(
    content: ScriptContent, settings: ProductionSettings, *, dialogue_enabled: bool
) -> float:
    """文字数 / (300文字/分 × speaking_rate) × 60 で推定尺(秒)を計算する。"""
    characters = count_script_characters(content, dialogue_enabled=dialogue_enabled)
    chars_per_second = (CHARS_PER_MINUTE * settings.speaking_rate) / 60.0
    if chars_per_second <= 0:
        return 0.0
    return characters / chars_per_second


def duration_within_range(estimated_seconds: float, settings: ProductionSettings) -> bool:
    """推定尺が `min_duration_seconds`〜`max_duration_seconds` の範囲内かを判定する。"""
    return settings.min_duration_seconds <= estimated_seconds <= settings.max_duration_seconds
