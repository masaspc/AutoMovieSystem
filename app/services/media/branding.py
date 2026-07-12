"""シリーズ統一ブランディング(サムネイル用アクセントカラー・レイアウト方針)。

同一シリーズの動画は常に同じ配色になり(チャンネルページで「番組」として認識できる)、
シリーズ間はパレット上できるだけ別の色になるよう、シリーズ名(または保存済み設定)から
決定的に配色を選ぶ。乱数は一切使わない(sha256ベースの決定的選択)。
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field

_HEX_PATTERN = r"^#[0-9A-Fa-f]{6}$"

# 高コントラストな配色パレット(アクセント色, 補色/バッジ色)。白文字+黒縁取りでも
# 可読性が確保できる十分な濃さのものを選定している。
_PALETTES: tuple[tuple[str, str], ...] = (
    ("#E63946", "#1D3557"),  # 情熱レッド × ネイビー
    ("#2A9D8F", "#264653"),  # ティール × ダークスレート
    ("#F4A261", "#264653"),  # オレンジ × ダークスレート
    ("#457B9D", "#1D3557"),  # ブルー × ネイビー
    ("#E76F51", "#2A9D8F"),  # コーラル × ティール
    ("#9B5DE5", "#3A0CA3"),  # パープル × ディープパープル
    ("#00B4D8", "#03045E"),  # シアン × ネイビー
    ("#FFB703", "#023047"),  # イエロー × ダークブルー
)

_TEMPLATES: tuple[Literal["bold", "clean", "pop"], ...] = ("bold", "clean", "pop")


class SeriesBranding(BaseModel):
    """サムネイル生成に使うシリーズ統一ビジュアルアイデンティティ。"""

    accent_color: str = Field(pattern=_HEX_PATTERN)
    secondary_color: str = Field(pattern=_HEX_PATTERN)
    text_color: str = Field(default="#FFFFFF", pattern=_HEX_PATTERN)
    template: Literal["bold", "clean", "pop"] = "bold"


def resolve_branding(series_name_or_id: str, stored: dict | None) -> SeriesBranding:
    """シリーズのブランディング設定を解決する。

    `stored`(`SeriesPlan.branding`)があればそれを優先して検証する。なければ
    `series_name_or_id` のsha256から決定的にパレットを選ぶ。同名シリーズ(または
    同名チャンネル)は常に同じ配色になり、シリーズ間は別の配色になりうる。
    """
    if stored:
        return SeriesBranding.model_validate(stored)

    digest = hashlib.sha256(series_name_or_id.encode("utf-8")).digest()
    accent_color, secondary_color = _PALETTES[digest[0] % len(_PALETTES)]
    template = _TEMPLATES[digest[1] % len(_TEMPLATES)]
    return SeriesBranding(
        accent_color=accent_color, secondary_color=secondary_color, template=template
    )
