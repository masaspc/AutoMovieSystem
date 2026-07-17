"""動画ごとの映像演出をsha256で決定論的に選ぶ。"""

from __future__ import annotations

import colorsys
import hashlib
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

KEN_BURNS_STYLES = (
    "zoom_in_left",
    "zoom_in_right",
    "zoom_out_left",
    "zoom_out_right",
)
TRANSITION_STYLES = ("fade", "wipeleft", "slideup", "circleopen")
HEADING_STYLES = ("fade", "slide_left", "pop")
ACCENT_HUE_SHIFTS = (-12, -6, 0, 6, 12)


@dataclass(frozen=True)
class VisualVarietyPlan:
    """1本の動画を通して統一する映像演出の抽選結果。"""

    ken_burns_style: str
    transition_style: str
    heading_style: str
    accent_hue_shift: int


def pick_visual_variety_plan(video_project_id: str) -> VisualVarietyPlan:
    """同じvideo_project_idなら常に同じ演出計画を返す。"""
    digest = hashlib.sha256(f"visual-variety:{video_project_id}".encode()).digest()
    return VisualVarietyPlan(
        ken_burns_style=KEN_BURNS_STYLES[digest[0] % len(KEN_BURNS_STYLES)],
        transition_style=TRANSITION_STYLES[digest[1] % len(TRANSITION_STYLES)],
        heading_style=HEADING_STYLES[digest[2] % len(HEADING_STYLES)],
        accent_hue_shift=ACCENT_HUE_SHIFTS[digest[3] % len(ACCENT_HUE_SHIFTS)],
    )


def shift_accent_hue(color: tuple[int, int, int], degrees: int) -> tuple[int, int, int]:
    """ブランド色の明度・彩度を保ち、色相だけを小幅にずらす。"""
    red, green, blue = (component / 255 for component in color)
    hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
    shifted = colorsys.hsv_to_rgb((hue + degrees / 360) % 1.0, saturation, value)
    shifted_red, shifted_green, shifted_blue = shifted
    return (
        round(shifted_red * 255),
        round(shifted_green * 255),
        round(shifted_blue * 255),
    )


def visual_layer_path(image_path: Path, layer: str) -> Path:
    """教材背景に対応する決定的なレイヤー画像名を返す。"""
    return image_path.with_name(f"{image_path.stem}_{layer}.png")


def animate_heading_overlay(
    heading: Image.Image, *, style: str, progress: float
) -> Image.Image:
    """透明見出しレイヤーをfade/slide/popで決定論的に変形する。"""
    source = heading.convert("RGBA")
    result = Image.new("RGBA", source.size, (0, 0, 0, 0))
    if style == "slide_left":
        result.alpha_composite(source, (-round(source.width * (1 - progress)), 0))
        return result
    if style == "pop":
        bounds = source.getchannel("A").getbbox()
        if bounds is None:
            return result
        crop = source.crop(bounds)
        scale = 0.65 + 0.35 * progress
        resized = crop.resize(
            (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
            Image.Resampling.LANCZOS,
        )
        center_x = (bounds[0] + bounds[2]) // 2
        center_y = (bounds[1] + bounds[3]) // 2
        result.alpha_composite(
            resized,
            (center_x - resized.width // 2, center_y - resized.height // 2),
        )
        return result
    alpha = source.getchannel("A").point(lambda value: round(value * progress))
    source.putalpha(alpha)
    return source


def ken_burns_filter(style: str) -> str:
    """1920x1080用の穏やかなzoompan式を返す。未知値は安全な既定へ戻す。"""
    expressions = {
        "zoom_in_left": ("min(zoom+0.00015,1.12)", "0"),
        "zoom_in_right": ("min(zoom+0.00015,1.12)", "iw-iw/zoom"),
        "zoom_out_left": ("if(eq(on,0),1.12,max(zoom-0.00015,1.0))", "0"),
        "zoom_out_right": (
            "if(eq(on,0),1.12,max(zoom-0.00015,1.0))",
            "iw-iw/zoom",
        ),
    }
    zoom, x_position = expressions.get(style, expressions["zoom_in_left"])
    return (
        f"zoompan=z='{zoom}':x='{x_position}':y='(ih-ih/zoom)/2':"
        "d=1:s=1920x1080"
    )
