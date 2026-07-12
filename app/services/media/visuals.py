"""台本の映像演出指示から、学習内容に対応するセクション背景を生成する。"""

from __future__ import annotations

import json
import keyword
import re
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


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    max_width: int,
    preferred_size: int,
    minimum_size: int = 20,
    bold: bool = False,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """指定幅へ収まるまでフォントを縮小する。"""
    for size in range(preferred_size, minimum_size - 1, -2):
        font = _font(size, bold=bold)
        if draw.textlength(text, font=font) <= max_width:
            return font
    return _font(minimum_size, bold=bold)


def _ellipsize(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> str:
    """1行テキストを必ず指定幅内へ収める。"""
    if draw.textlength(text, font=font) <= max_width:
        return text
    suffix = "…"
    shortened = text
    while shortened and draw.textlength(shortened + suffix, font=font) > max_width:
        shortened = shortened[:-1]
    return shortened + suffix


def _wrap_pixels(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    *,
    max_width: int,
    max_lines: int,
) -> list[str]:
    """日本語を含む文章を文字幅ベースで折り返し、最終行を省略表示する。"""
    remaining = text.strip()
    lines: list[str] = []
    while remaining and len(lines) < max_lines:
        length = 1
        while length <= len(remaining) and draw.textlength(
            remaining[:length], font=font
        ) <= max_width:
            length += 1
        take = max(1, length - 1)
        line = remaining[:take]
        remaining = remaining[take:].lstrip()
        if len(lines) == max_lines - 1 and remaining:
            line = _ellipsize(draw, line + remaining, font, max_width)
            remaining = ""
        lines.append(line)
    return lines or [""]


def _draw_header(draw: ImageDraw.ImageDraw, title: str, accent: tuple[int, int, int]) -> None:
    draw.rounded_rectangle((90, 65, 1830, 190), radius=28, fill=(*accent, 235))
    font = _fit_font(draw, title, max_width=1650, preferred_size=48, bold=True)
    draw.text((135, 93), _ellipsize(draw, title, font, 1650), fill="white", font=font)


def _code_from_section(section: dict) -> str:
    code = str(section.get("code") or "").strip()
    if code:
        return code
    instruction = str(section.get("visual_instruction") or "")
    candidates = re.findall(r"「([^」]+)」", instruction, flags=re.DOTALL)
    code_candidates = [
        candidate.strip()
        for candidate in candidates
        if re.search(r"(?:print|input|int|float|str|=|\(|\))", candidate)
    ]
    return "\n".join(code_candidates)


def _draw_code(
    draw: ImageDraw.ImageDraw, section: dict, accent: tuple[int, int, int]
) -> None:
    code = _code_from_section(section)
    if not code:
        _draw_bullets(draw, section, accent)
        return
    draw.rounded_rectangle(
        (150, 235, 1770, 875),
        radius=24,
        fill=(12, 15, 22),
        outline=(70, 78, 92),
        width=3,
    )
    highlights = {int(value) for value in section.get("highlight_lines") or []}
    for number, line in enumerate(code.splitlines()[:14], start=1):
        y = 280 + (number - 1) * 40
        if number in highlights:
            draw.rounded_rectangle((175, y - 5, 1735, y + 35), radius=8, fill=(45, 68, 48))
        draw.text((195, y), f"{number:>2}", fill=(112, 122, 140), font=_font(25))
        _draw_python_line(draw, line, 270, y, max_width=1430)


_CODE_TOKEN_RE = re.compile(r"(#[^\n]*|(?:\"[^\"]*\"|'[^']*')|\b\d+(?:\.\d+)?\b|\b[A-Za-z_]\w*\b)")


def _draw_python_line(
    draw: ImageDraw.ImageDraw, line: str, x: int, y: int, *, max_width: int
) -> None:
    """標準的なPythonトークンを色分けして1行描画する。"""
    font = _fit_font(draw, line, max_width=max_width, preferred_size=27, minimum_size=18)
    line = _ellipsize(draw, line, font, max_width)
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
    visible_bullets = bullets[:5]
    slot_height = min(125, 630 // max(1, len(visible_bullets)))
    for index, bullet in enumerate(visible_bullets, start=1):
        top = 255 + (index - 1) * slot_height
        bottom = top + slot_height - 18
        draw.rounded_rectangle(
            (190, top, 1730, bottom),
            radius=22,
            fill=(255, 255, 255, 28),
            outline=accent,
            width=3,
        )
        center_y = (top + bottom) // 2
        draw.ellipse((225, center_y - 25, 275, center_y + 25), fill=accent)
        draw.text((242, center_y - 18), str(index), fill="white", font=_font(23))
        bullet_text = str(bullet)
        font = _font(30)
        lines = _wrap_pixels(draw, bullet_text, font, max_width=1360, max_lines=2)
        line_height = 38
        text_y = center_y - (len(lines) * line_height) // 2
        for line in lines:
            draw.text((310, text_y), line, fill=(242, 245, 250), font=font)
            text_y += line_height


def _draw_quiz(draw: ImageDraw.ImageDraw, section: dict, accent: tuple[int, int, int]) -> None:
    question = str(
        section.get("quiz_question") or section.get("visual_title") or "ここで確認問題です"
    )
    y = 260
    question_font = _font(42)
    for line in _wrap_pixels(draw, question, question_font, max_width=1500, max_lines=3):
        draw.text((180, y), line, fill=(250, 250, 255), font=question_font)
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
        option_text = f"{chr(65 + index)}. {str(option)}"
        option_font = _fit_font(draw, option_text, max_width=1320, preferred_size=30)
        draw.text(
            (300, top + 17),
            _ellipsize(draw, option_text, option_font, 1320),
            fill="white",
            font=option_font,
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
        _draw_code(draw, section, accent)
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
