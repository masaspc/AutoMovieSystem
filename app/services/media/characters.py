"""立ち絵キャラクターパックを読み込み、掛け合い用のフレームを生成する。

配置の方針(運用フィードバック反映):
- 掛け合いの2人は画面の両端に立ち、右側のキャラクターは左右反転して内側(相手側)を
  向く(反転はPILで行うため反転済み素材は不要)。
- キャラクターは途中で消えない。コード表示等のビジュアル重視セクション
  (character_layout が full 以外)でも、小さくなって両端に残る。
- 立ち絵は上下に動かさない(口パク・瞬きの差分表示のみ。素材サイズが差分間で
  異なっても、下端アンカー位置は共通サイズ枠で固定する)。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.core.config import Settings
from app.services.media.dialogue import SpeechLine
from app.services.media.renderer import (
    VIDEO_HEIGHT_16_9,
    VIDEO_WIDTH_16_9,
    SceneFrame,
    find_japanese_font,
)


class CharacterAssetError(ValueError):
    """キャラクター描画が有効なのに、必要な利用許諾済み素材が存在しない。"""


_CHARACTER_NAMES = {
    "zundamon": "ずんだもん",
    "metan": "四国めたん",
    "tsumugi": "春日部つむぎ",
}
_CHARACTER_CREDITS = {
    "zundamon": "VOICEVOX:ずんだもん",
    "metan": "VOICEVOX:四国めたん",
    "tsumugi": "VOICEVOX:春日部つむぎ",
}
# 画面上の立ち位置。left/rightは両端、centerは中央(つむぎはセリフがある時だけ登場)。
_SIDES = {
    "zundamon": "left",
    "metan": "right",
    "tsumugi": "center",
}
_SIDE_ORDER = {"left": 0, "center": 1, "right": 2}
_EDGE_MARGIN = 60
_BOTTOM_MARGIN = 60


def character_credits(lines: list[SpeechLine]) -> list[str]:
    """登場話者に必要なVOICEVOXクレジットを重複なく返す。"""
    credits = {
        _CHARACTER_CREDITS[line.speaker] for line in lines if line.speaker in _CHARACTER_CREDITS
    }
    return sorted(credits)


def _character_dir(settings: Settings, speaker: str) -> Path:
    if speaker not in _CHARACTER_NAMES:
        raise CharacterAssetError(f"未対応のキャラクターです: {speaker}")
    return Path(settings.CHARACTER_ASSETS_DIR) / speaker


def _first_existing(directory: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _portrait_path(settings: Settings, speaker: str, emotion: str, *, talking: bool) -> Path:
    directory = _character_dir(settings, speaker)
    names = (
        (f"{emotion}_open.png", "mouth_open.png", "talk.png", f"{emotion}.png", "normal.png")
        if talking
        else (f"{emotion}.png", "normal.png")
    )
    portrait = _first_existing(directory, names)
    if portrait is None:
        raise CharacterAssetError(
            f"{_CHARACTER_NAMES[speaker]}の立ち絵がありません: {directory} "
            "(最低限 normal.png を配置してください)"
        )
    return portrait


def _blink_path(settings: Settings, speaker: str, emotion: str) -> Path | None:
    return _first_existing(
        _character_dir(settings, speaker),
        (f"{emotion}_blink.png", "blink.png"),
    )


def character_assets_fingerprint(settings: Settings, lines: list[SpeechLine]) -> str:
    """使う立ち絵の内容をレンダリング冪等性の入力に含める。"""
    if not settings.CHARACTER_RENDER_ENABLED:
        return "characters-disabled"
    hasher = hashlib.sha256()
    for line in lines:
        for talking in (False, True):
            portrait = _portrait_path(settings, line.speaker, line.emotion, talking=talking)
            hasher.update(str(portrait.resolve()).encode("utf-8"))
            hasher.update(portrait.read_bytes())
        blink = _blink_path(settings, line.speaker, line.emotion)
        if blink is not None:
            hasher.update(str(blink.resolve()).encode("utf-8"))
            hasher.update(blink.read_bytes())
    return hasher.hexdigest()


def _fit_portrait(image: Image.Image, *, active: bool, compact: bool, mirror: bool) -> Image.Image:
    portrait = image.convert("RGBA")
    max_size = (330, 570) if compact else (520, 780)
    portrait.thumbnail(max_size, Image.Resampling.LANCZOS)
    if mirror:
        # 右側に立つキャラクターは内側(相手側)を向くよう左右反転する(反転素材は不要)。
        portrait = ImageOps.mirror(portrait)
    if not active:
        alpha = portrait.getchannel("A").point(lambda value: value * 0.45)
        portrait.putalpha(alpha)
    return portrait


def _draw_nameplate(canvas: Image.Image, *, speaker: str, active: bool, x: int) -> None:
    draw = ImageDraw.Draw(canvas)
    x = max(10, min(x, VIDEO_WIDTH_16_9 - 320))
    color = (86, 196, 125, 235) if active else (65, 72, 90, 190)
    draw.rounded_rectangle((x, 205, x + 310, 267), radius=12, fill=color)
    try:
        font_path = find_japanese_font()
        font = ImageFont.truetype(font_path, 28) if font_path else ImageFont.load_default()
        draw.text((x + 18, 221), _CHARACTER_NAMES[speaker], fill=(255, 255, 255), font=font)
    except Exception:  # noqa: BLE001 - フォントがない環境でも動画生成を継続する
        draw.text((x + 18, 221), speaker, fill=(255, 255, 255), font=ImageFont.load_default())


def _portrait_x(side: str, portrait_width: int) -> int:
    if side == "left":
        return _EDGE_MARGIN
    if side == "right":
        return VIDEO_WIDTH_16_9 - portrait_width - _EDGE_MARGIN
    return (VIDEO_WIDTH_16_9 - portrait_width) // 2


def _compose_frame(
    *,
    background: Path,
    lines: list[SpeechLine],
    current: SpeechLine,
    talking: bool,
    settings: Settings,
    output_path: Path,
    section: dict,
    blinking: bool = False,
) -> Path:
    with Image.open(background) as source:
        canvas = source.convert("RGBA").resize((VIDEO_WIDTH_16_9, VIDEO_HEIGHT_16_9))
    layout = str(section.get("character_layout") or "full")
    # キャラクターは途中で消さない: full以外(コード表示・図解など)でも小さくして
    # 両端に残す(視聴者が「番組の登場人物」を見失わないようにする)。
    compact = layout != "full"
    speakers = {line.speaker for line in lines if line.speaker != "tsumugi"}
    if current.speaker == "tsumugi":
        speakers.add("tsumugi")
    for speaker in sorted(speakers, key=lambda value: _SIDE_ORDER[_SIDES[value]]):
        active = speaker == current.speaker
        emotion = current.emotion if active else "neutral"
        portrait_path = (
            _blink_path(settings, speaker, emotion) if active and blinking else None
        ) or _portrait_path(settings, speaker, emotion, talking=talking and active)
        side = _SIDES[speaker]
        with Image.open(portrait_path) as source:
            portrait = _fit_portrait(
                source, active=active, compact=compact, mirror=(side == "right")
            )
        x = _portrait_x(side, portrait.width)
        # 上下の動きは付けない(口パク・瞬きの差分のみ)。下端アンカーで固定する。
        y = VIDEO_HEIGHT_16_9 - portrait.height - _BOTTOM_MARGIN
        canvas.alpha_composite(portrait, (x, y))
        if not compact:
            _draw_nameplate(canvas, speaker=speaker, active=active, x=x)

    if current.emotion != "neutral":
        draw = ImageDraw.Draw(canvas)
        icon = {"happy": "♪", "serious": "!", "surprised": "!?"}.get(current.emotion, "")
        if icon:
            draw.ellipse((1720, 285, 1835, 400), fill=(255, 220, 80, 235))
            font_path = find_japanese_font()
            icon_font = (
                ImageFont.truetype(font_path, 48)
                if font_path is not None
                else ImageFont.load_default()
            )
            draw.text((1750, 305), icon, fill=(35, 35, 45), font=icon_font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, format="PNG")
    return output_path


def build_scene_frames(
    *,
    backgrounds: dict[int, Path] | None = None,
    background: Path | None = None,
    lines: list[SpeechLine],
    durations: list[float],
    sections: list[dict] | None = None,
    settings: Settings,
    output_dir: Path,
) -> list[SceneFrame]:
    """セリフ単位で話者を強調したフレーム列を作る。open素材があれば交互表示する。"""
    if not settings.CHARACTER_RENDER_ENABLED:
        return []
    if len(lines) != len(durations):
        raise CharacterAssetError("セリフと音声尺の数が一致しません")
    backgrounds = dict(backgrounds or {})
    if background is not None and not backgrounds:
        backgrounds[0] = background
    if not backgrounds:
        raise CharacterAssetError("背景画像がありません")
    sections = sections or []

    frames: list[SceneFrame] = []
    for line, duration in zip(lines, durations, strict=True):
        section = sections[line.section_index] if line.section_index < len(sections) else {}
        background_path = backgrounds.get(line.section_index) or next(iter(backgrounds.values()))
        closed = _compose_frame(
            background=background_path,
            lines=lines,
            current=line,
            talking=False,
            settings=settings,
            output_path=output_dir / f"line_{line.index:03d}_closed.png",
            section=section,
        )
        open_path = _portrait_path(settings, line.speaker, line.emotion, talking=True)
        closed_path = _portrait_path(settings, line.speaker, line.emotion, talking=False)
        if open_path == closed_path or duration <= 0.3:
            frames.append(SceneFrame(path=closed, duration_seconds=duration))
            continue

        opened = _compose_frame(
            background=background_path,
            lines=lines,
            current=line,
            talking=True,
            settings=settings,
            output_path=output_dir / f"line_{line.index:03d}_open.png",
            section=section,
        )
        blink_source = _blink_path(settings, line.speaker, line.emotion)
        blink = (
            _compose_frame(
                background=background_path,
                lines=lines,
                current=line,
                talking=False,
                settings=settings,
                output_path=output_dir / f"line_{line.index:03d}_blink.png",
                section=section,
                blinking=True,
            )
            if blink_source is not None
            else None
        )
        remaining = duration
        mouth_open = False
        phase = 0
        while remaining > 0:
            clip_duration = min(0.18, remaining)
            frame_path = (
                blink
                if blink is not None and phase > 0 and phase % 18 == 0
                else (opened if mouth_open else closed)
            )
            frames.append(
                SceneFrame(
                    path=frame_path,
                    duration_seconds=clip_duration,
                )
            )
            mouth_open = not mouth_open
            phase += 1
            remaining -= clip_duration
    return frames
