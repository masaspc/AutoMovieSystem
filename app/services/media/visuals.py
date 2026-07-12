"""台本の映像演出指示から、学習内容に対応するセクション背景を生成する。"""

from __future__ import annotations

import json
import keyword
import re
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.services.media.renderer import (
    VIDEO_HEIGHT_16_9,
    VIDEO_WIDTH_16_9,
    SceneFrame,
    find_japanese_font,
)

_PALETTES = {
    "classroom": ((22, 29, 48), (69, 86, 128)),
    "editor": ((20, 23, 31), (76, 175, 80)),
    "card": ((22, 35, 57), (66, 153, 225)),
    "quiz": ((42, 28, 63), (171, 113, 230)),
    "diagram": ((17, 43, 48), (38, 166, 154)),
}


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = find_japanese_font()
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _wrapped(text: str, width: int) -> list[str]:
    return textwrap.wrap(text.strip(), width=width) or [""]


def _draw_header(draw: ImageDraw.ImageDraw, title: str, accent: tuple[int, int, int]) -> None:
    draw.rounded_rectangle((90, 65, 1830, 190), radius=28, fill=(*accent, 235))
    draw.text((135, 93), title[:44], fill="white", font=_font(48, bold=True))


def _draw_code(draw: ImageDraw.ImageDraw, section: dict) -> None:
    draw.rounded_rectangle(
        (150, 235, 1770, 875),
        radius=24,
        fill=(12, 15, 22),
        outline=(70, 78, 92),
        width=3,
    )
    code = str(section.get("code") or section.get("visual_instruction") or "# コード例")
    highlights = {int(value) for value in section.get("highlight_lines") or []}
    for number, line in enumerate(code.splitlines()[:14], start=1):
        y = 280 + (number - 1) * 40
        if number in highlights:
            draw.rounded_rectangle((175, y - 5, 1735, y + 35), radius=8, fill=(45, 68, 48))
        draw.text((195, y), f"{number:>2}", fill=(112, 122, 140), font=_font(25))
        _draw_python_line(draw, line[:78], 270, y)


_CODE_TOKEN_RE = re.compile(r"(#[^\n]*|(?:\"[^\"]*\"|'[^']*')|\b\d+(?:\.\d+)?\b|\b[A-Za-z_]\w*\b)")


def _draw_python_line(draw: ImageDraw.ImageDraw, line: str, x: int, y: int) -> None:
    """標準的なPythonトークンを色分けして1行描画する。"""
    font = _font(27)
    cursor = float(x)
    last = 0
    for match in _CODE_TOKEN_RE.finditer(line):
        plain = line[last : match.start()]
        draw.text((cursor, y), plain, fill=(225, 230, 240), font=font)
        cursor += draw.textlength(plain, font=font)
        token = match.group(0)
        if token.startswith("#"):
            color = (118, 150, 105)
        elif token[:1] in {'"', "'"}:
            color = (206, 145, 120)
        elif token[0].isdigit():
            color = (181, 206, 168)
        elif keyword.iskeyword(token):
            color = (197, 134, 192)
        else:
            color = (86, 156, 214)
        draw.text((cursor, y), token, fill=color, font=font)
        cursor += draw.textlength(token, font=font)
        last = match.end()
    draw.text((cursor, y), line[last:], fill=(225, 230, 240), font=font)


def _draw_bullets(draw: ImageDraw.ImageDraw, section: dict, accent: tuple[int, int, int]) -> None:
    bullets = section.get("visual_bullets") or [
        section.get("visual_instruction") or "要点を確認しましょう"
    ]
    for index, bullet in enumerate(bullets[:5], start=1):
        top = 255 + (index - 1) * 125
        draw.rounded_rectangle(
            (190, top, 1730, top + 92),
            radius=22,
            fill=(255, 255, 255, 28),
            outline=accent,
            width=3,
        )
        draw.ellipse((225, top + 20, 275, top + 70), fill=accent)
        draw.text((242, top + 27), str(index), fill="white", font=_font(23))
        draw.text((310, top + 24), str(bullet)[:52], fill=(242, 245, 250), font=_font(34))


def _draw_quiz(draw: ImageDraw.ImageDraw, section: dict, accent: tuple[int, int, int]) -> None:
    question = str(
        section.get("quiz_question") or section.get("visual_title") or "ここで確認問題です"
    )
    y = 260
    for line in _wrapped(question, 28)[:3]:
        draw.text((180, y), line, fill=(250, 250, 255), font=_font(42))
        y += 58
    for index, option in enumerate((section.get("quiz_options") or ["考えてみましょう"])[:4]):
        top = 470 + index * 105
        draw.rounded_rectangle(
            (260, top, 1660, top + 75),
            radius=18,
            fill=(255, 255, 255, 25),
            outline=accent,
            width=2,
        )
        draw.text(
            (300, top + 17),
            f"{chr(65 + index)}. {str(option)[:42]}",
            fill="white",
            font=_font(30),
        )


def _draw_chart(draw: ImageDraw.ImageDraw, section: dict, accent: tuple[int, int, int]) -> None:
    labels = [str(value) for value in section.get("chart_labels") or []][:8]
    raw_values = section.get("chart_values") or []
    values = [float(value) for value in raw_values[: len(labels)]]
    if not labels or len(labels) != len(values):
        _draw_bullets(draw, section, accent)
        return
    chart_title = str(section.get("chart_title") or section.get("visual_title") or "比較")
    draw.text((190, 235), chart_title[:40], fill="white", font=_font(36, bold=True))
    left, top, right, bottom = 230, 330, 1690, 835
    draw.line((left, top, left, bottom), fill=(190, 200, 215), width=3)
    draw.line((left, bottom, right, bottom), fill=(190, 200, 215), width=3)
    maximum = max(max(values), 1.0)
    slot = (right - left) / len(values)
    for index, (label, value) in enumerate(zip(labels, values, strict=True)):
        bar_width = min(120, int(slot * 0.62))
        x0 = int(left + index * slot + (slot - bar_width) / 2)
        height = int((bottom - top - 55) * max(0.0, value) / maximum)
        y0 = bottom - height
        draw.rounded_rectangle((x0, y0, x0 + bar_width, bottom), radius=12, fill=accent)
        value_text = f"{value:g}"
        draw.text((x0, y0 - 42), value_text, fill="white", font=_font(25))
        draw.text((x0, bottom + 18), label[:9], fill=(230, 235, 245), font=_font(23))


def _draw_emphasis_words(
    draw: ImageDraw.ImageDraw, section: dict, accent: tuple[int, int, int]
) -> None:
    """字幕とは独立した短いキーワードテロップを中央セーフエリアへ表示する。"""
    words = [
        str(word).strip()
        for word in section.get("emphasis_words") or []
        if str(word).strip()
    ]
    if not words:
        return
    font = _font(30, bold=True)
    words = words[:3]
    widths = [int(draw.textlength(word[:18], font=font)) + 52 for word in words]
    gap = 18
    total_width = sum(widths) + gap * (len(widths) - 1)
    x = max(500, (VIDEO_WIDTH_16_9 - total_width) // 2)
    y = 930
    for word, width in zip(words, widths, strict=True):
        draw.rounded_rectangle((x, y, x + width, y + 64), radius=25, fill=(*accent, 235))
        draw.text((x + 26, y + 14), word[:18], fill="white", font=font)
        x += width + gap


def generate_section_visual(section: dict, output_path: Path) -> Path:
    """セクションのvisual_typeに応じた1920x1080教材背景を生成する。"""
    style = str(section.get("background_style") or "classroom")
    base, accent = _PALETTES.get(style, _PALETTES["classroom"])
    image = Image.new("RGB", (VIDEO_WIDTH_16_9, VIDEO_HEIGHT_16_9), base)
    draw = ImageDraw.Draw(image, "RGBA")
    draw.ellipse((-250, -400, 900, 750), fill=(*accent, 26))
    draw.ellipse((1350, 600, 2200, 1350), fill=(*accent, 22))
    title = str(section.get("visual_title") or section.get("heading") or "学習ポイント")
    _draw_header(draw, title, accent)
    visual_type = str(section.get("visual_type") or "dialogue")
    if visual_type == "code":
        _draw_code(draw, section)
    elif visual_type == "quiz":
        _draw_quiz(draw, section, accent)
    elif visual_type == "chart":
        _draw_chart(draw, section, accent)
    else:
        _draw_bullets(draw, section, accent)
    _draw_emphasis_words(draw, section, accent)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, "PNG")
    return output_path


def write_scene_manifest(
    sections: list[dict], line_section_indexes: list[int], durations: list[float], output_path: Path
) -> Path:
    """視聴維持率と照合できる、実時間ベースの場面タイムラインを保存する。"""
    elapsed = 0.0
    scenes: list[dict] = []
    for line_index, (section_index, duration) in enumerate(
        zip(line_section_indexes, durations, strict=True)
    ):
        section = sections[section_index] if section_index < len(sections) else {}
        scenes.append(
            {
                "line_index": line_index,
                "section_index": section_index,
                "heading": section.get("heading", ""),
                "visual_type": section.get("visual_type", "dialogue"),
                "start_seconds": round(elapsed, 3),
                "end_seconds": round(elapsed + duration, 3),
            }
        )
        elapsed += duration
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {"duration_seconds": elapsed, "scenes": scenes},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return output_path


def build_background_frames(
    backgrounds: dict[int, Path], line_section_indexes: list[int], durations: list[float]
) -> list[SceneFrame]:
    """立ち絵を使わない場合も、発話区間ごとに教材背景を切り替える。"""
    if len(line_section_indexes) != len(durations):
        raise ValueError("セクション位置と音声尺の数が一致しません")
    if not backgrounds:
        raise ValueError("背景画像がありません")
    fallback = next(iter(backgrounds.values()))
    return [
        SceneFrame(
            path=backgrounds.get(section_index, fallback),
            duration_seconds=duration,
        )
        for section_index, duration in zip(line_section_indexes, durations, strict=True)
    ]
