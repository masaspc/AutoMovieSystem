"""立ち絵キャラクターパックを読み込み、掛け合い用のフレームを生成する。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

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
_POSITIONS = {
    "zundamon": 90,
    "tsumugi": 700,
    "metan": 1320,
}


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
    return hasher.hexdigest()


def _fit_portrait(image: Image.Image, *, active: bool) -> Image.Image:
    portrait = image.convert("RGBA")
    portrait.thumbnail((520, 880), Image.Resampling.LANCZOS)
    if not active:
        alpha = portrait.getchannel("A").point(lambda value: value * 0.45)
        portrait.putalpha(alpha)
    return portrait


def _draw_nameplate(canvas: Image.Image, *, speaker: str, active: bool) -> None:
    draw = ImageDraw.Draw(canvas)
    x = _POSITIONS[speaker]
    color = (86, 196, 125, 235) if active else (65, 72, 90, 190)
    draw.rounded_rectangle((x, 900, x + 310, 962), radius=12, fill=color)
    try:
        font_path = find_japanese_font()
        font = ImageFont.truetype(font_path, 28) if font_path else ImageFont.load_default()
        draw.text((x + 18, 916), _CHARACTER_NAMES[speaker], fill=(255, 255, 255), font=font)
    except Exception:  # noqa: BLE001 - フォントがない環境でも動画生成を継続する
        draw.text((x + 18, 916), speaker, fill=(255, 255, 255), font=ImageFont.load_default())


def _compose_frame(
    *,
    background: Path,
    lines: list[SpeechLine],
    current: SpeechLine,
    talking: bool,
    settings: Settings,
    output_path: Path,
) -> Path:
    with Image.open(background) as source:
        canvas = source.convert("RGBA").resize((VIDEO_WIDTH_16_9, VIDEO_HEIGHT_16_9))
    speakers = {line.speaker for line in lines if line.speaker != "tsumugi"}
    if current.speaker == "tsumugi":
        speakers.add("tsumugi")
    for speaker in sorted(speakers, key=lambda value: _POSITIONS[value]):
        active = speaker == current.speaker
        emotion = current.emotion if active else "neutral"
        portrait_path = _portrait_path(settings, speaker, emotion, talking=talking and active)
        with Image.open(portrait_path) as source:
            portrait = _fit_portrait(source, active=active)
        x = _POSITIONS[speaker] + (520 - portrait.width) // 2
        y = VIDEO_HEIGHT_16_9 - portrait.height - 82
        canvas.alpha_composite(portrait, (x, y))
        _draw_nameplate(canvas, speaker=speaker, active=active)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, format="PNG")
    return output_path


def build_scene_frames(
    *,
    background: Path,
    lines: list[SpeechLine],
    durations: list[float],
    settings: Settings,
    output_dir: Path,
) -> list[SceneFrame]:
    """セリフ単位で話者を強調したフレーム列を作る。open素材があれば交互表示する。"""
    if not settings.CHARACTER_RENDER_ENABLED:
        return []
    if len(lines) != len(durations):
        raise CharacterAssetError("セリフと音声尺の数が一致しません")

    frames: list[SceneFrame] = []
    for line, duration in zip(lines, durations, strict=True):
        closed = _compose_frame(
            background=background,
            lines=lines,
            current=line,
            talking=False,
            settings=settings,
            output_path=output_dir / f"line_{line.index:03d}_closed.png",
        )
        open_path = _portrait_path(settings, line.speaker, line.emotion, talking=True)
        closed_path = _portrait_path(settings, line.speaker, line.emotion, talking=False)
        if open_path == closed_path or duration <= 0.3:
            frames.append(SceneFrame(path=closed, duration_seconds=duration))
            continue

        opened = _compose_frame(
            background=background,
            lines=lines,
            current=line,
            talking=True,
            settings=settings,
            output_path=output_dir / f"line_{line.index:03d}_open.png",
        )
        remaining = duration
        mouth_open = False
        while remaining > 0:
            clip_duration = min(0.18, remaining)
            frames.append(
                SceneFrame(
                    path=opened if mouth_open else closed,
                    duration_seconds=clip_duration,
                )
            )
            mouth_open = not mouth_open
            remaining -= clip_duration
    return frames
