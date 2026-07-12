"""動画の尺・台本自由度に関するユーザー設定(Phase 2)。

`VideoProject.production_settings`(JSON列)に保存し、台本生成プロンプトの
組み立て(`app/services/scripts/generator.py`)と尺検査(`app/services/scripts/duration.py`)
の両方から参照する。プリセットは短尺・標準3種・カスタムの4系統。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# 日本語ナレーション280〜330文字/分の中央値。台本の文字数見積のみに使う
# (TTS合成後の実測尺を正とする。app/services/scripts/duration.py参照)。
CHARS_PER_MINUTE = 300

PresetName = Literal["short", "standard_3min", "standard_5min", "standard_8min", "custom"]
VideoFormat = Literal["short", "standard", "custom"]
ScriptTemplate = Literal[
    "explainer",
    "ranking",
    "problem_solution",
    "comparison",
    "story",
    "dialogue",
    "shorts",
    "trivia",
    "news_commentary",
]

# preset -> video_format の導出テーブル。
_PRESET_VIDEO_FORMAT: dict[str, VideoFormat] = {
    "short": "short",
    "standard_3min": "standard",
    "standard_5min": "standard",
    "standard_8min": "standard",
    "custom": "custom",
}

# preset -> (target, min, max, min_sections, max_sections) のデフォルト値。
# custom は target_duration_seconds のデフォルトのみ持ち、min/max は
# ±15% を _fill_defaults で計算する(ユーザーが明示指定すればそちらを優先)。
_PRESET_DEFAULTS: dict[str, dict[str, int]] = {
    "short": {
        "target_duration_seconds": 45,
        "min_duration_seconds": 30,
        "max_duration_seconds": 60,
        "min_sections": 1,
        "max_sections": 3,
    },
    "standard_3min": {
        "target_duration_seconds": 180,
        "min_duration_seconds": 150,
        "max_duration_seconds": 210,
        "min_sections": 3,
        "max_sections": 5,
    },
    "standard_5min": {
        "target_duration_seconds": 300,
        "min_duration_seconds": 270,
        "max_duration_seconds": 330,
        "min_sections": 4,
        "max_sections": 7,
    },
    "standard_8min": {
        "target_duration_seconds": 480,
        "min_duration_seconds": 420,
        "max_duration_seconds": 540,
        "min_sections": 5,
        "max_sections": 9,
    },
    "custom": {
        # customのtarget_duration_secondsデフォルト値(未指定時)。min/maxは±15%で導出する。
        "target_duration_seconds": 60,
        "min_sections": 1,
        "max_sections": 5,
    },
}

_CUSTOM_RANGE_RATIO = 0.15


class ProductionSettings(BaseModel):
    """動画の尺・台本テンプレート等、ユーザーが選ぶ制作方針一式。

    全フィールドにデフォルトがあり、`ProductionSettings()` は `preset="short"`
    相当(現行挙動)になる。`VideoProject.production_settings` JSON列に
    `model_dump()` してそのまま保存する。
    """

    preset: PresetName = "short"
    # 以下6フィールドの実際のデフォルト値はpresetから `_fill_preset_defaults` が導出する。
    # ここに書いた値は preset="short" 相当(フィールド宣言上の安全側フォールバック)。
    video_format: VideoFormat = "short"
    target_duration_seconds: int = Field(default=45, ge=10, le=3600)
    min_duration_seconds: int = Field(default=30, ge=1, le=3600)
    max_duration_seconds: int = Field(default=60, ge=1, le=3600)
    target_character_count: int | None = Field(default=None, ge=1, le=100_000)
    min_sections: int = Field(default=1, ge=1, le=50)
    max_sections: int = Field(default=3, ge=1, le=50)
    # Phase 2では推定尺計算のみに使用(TTSへの適用は将来対応)。
    speaking_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    script_template: ScriptTemplate = "explainer"
    tone: str = Field(default="丁寧でわかりやすい", min_length=1, max_length=200)
    # 掛け合い台本時のセリフ比率ヒント(0.0〜1.0)。
    dialogue_ratio: float = Field(default=0.5, ge=0.0, le=1.0)

    # --- 音響(Phase A) ---
    # BGMの雰囲気。assets/bgm/<mood>/ から決定的に選曲される。"none"でBGMなし。
    bgm_mood: Literal["none", "calm", "upbeat", "serious"] = "calm"
    # セリフに対するBGMの基準音量(dB)。セリフ中はさらにダッキングで自動的に下がる。
    bgm_volume_db: float = Field(default=-19.0, ge=-40.0, le=0.0)
    # セクション切替などの効果音。
    se_enabled: bool = True
    se_volume_db: float = Field(default=-10.0, ge=-40.0, le=0.0)

    @model_validator(mode="before")
    @classmethod
    def _fill_preset_defaults(cls, data: Any) -> Any:
        """未指定フィールドをpresetのデフォルト値で埋める(引数なし構築=shortを許可)。"""
        if not isinstance(data, dict):
            return data

        merged = dict(data)
        preset = merged.get("preset", "short")
        defaults = _PRESET_DEFAULTS.get(preset, _PRESET_DEFAULTS["short"])

        merged.setdefault("video_format", _PRESET_VIDEO_FORMAT.get(preset, "custom"))
        for key in ("target_duration_seconds", "min_sections", "max_sections"):
            if key in defaults:
                merged.setdefault(key, defaults[key])

        if preset == "custom":
            target = merged.get("target_duration_seconds", defaults["target_duration_seconds"])
            merged.setdefault("min_duration_seconds", round(target * (1 - _CUSTOM_RANGE_RATIO)))
            merged.setdefault("max_duration_seconds", round(target * (1 + _CUSTOM_RANGE_RATIO)))
        else:
            merged.setdefault("min_duration_seconds", defaults["min_duration_seconds"])
            merged.setdefault("max_duration_seconds", defaults["max_duration_seconds"])

        return merged

    @model_validator(mode="after")
    def _validate_ranges(self) -> ProductionSettings:
        if not (
            self.min_duration_seconds <= self.target_duration_seconds <= self.max_duration_seconds
        ):
            raise ValueError(
                "min_duration_seconds <= target_duration_seconds <= "
                "max_duration_seconds で指定してください"
            )
        if self.min_sections > self.max_sections:
            raise ValueError("min_sections は max_sections 以下で指定してください")
        expected_format = _PRESET_VIDEO_FORMAT[self.preset]
        if self.video_format != expected_format:
            raise ValueError(
                f"preset={self.preset} の video_format は {expected_format} で指定してください"
            )
        return self

    @classmethod
    def from_preset(cls, preset: PresetName) -> ProductionSettings:
        """指定プリセットのデフォルト値を展開して構築する。"""
        return cls(preset=preset)

    def resolved_target_character_count(self) -> int:
        """`target_character_count` が未指定の場合に目標尺から導出した文字数を返す。

        日本語300文字/分基準 × speaking_rate。
        """
        if self.target_character_count is not None:
            return self.target_character_count
        minutes = self.target_duration_seconds / 60.0
        return round(minutes * CHARS_PER_MINUTE * self.speaking_rate)

    def checksum(self) -> str:
        """設定値のSHA256(正規化JSON: sort_keys・ensure_ascii=False)。

        設定内容が変われば台本生成の冪等キーも変わる(再生成のトリガーに使う)。
        """
        normalized = json.dumps(self.model_dump(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
