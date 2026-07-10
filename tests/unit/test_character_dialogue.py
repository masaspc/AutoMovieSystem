from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.core.config import Settings
from app.services.media.characters import build_scene_frames, character_credits
from app.services.media.dialogue import extract_speech_lines


def _write_portrait(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (400, 800), (*color, 255)).save(path)


def test_dialogue_lines_prefer_dialogue_and_keep_legacy_narration_when_enabled() -> None:
    lines = extract_speech_lines(
        {
            "sections": [
                {
                    "narration": "旧形式",
                    "dialogue": [
                        {"speaker": "zundamon", "text": "こんにちは", "emotion": "happy"},
                        {"speaker": "metan", "text": "解説します", "emotion": "serious"},
                    ],
                },
                {"narration": "互換用のナレーション"},
            ]
        },
        dialogue_enabled=True,
    )

    assert [(line.speaker, line.text) for line in lines] == [
        ("zundamon", "こんにちは"),
        ("metan", "解説します"),
        ("zundamon", "互換用のナレーション"),
    ]
    assert character_credits(lines) == ["VOICEVOX:ずんだもん", "VOICEVOX:四国めたん"]


def test_dialogue_lines_use_narration_when_dialogue_is_disabled() -> None:
    lines = extract_speech_lines(
        {
            "sections": [
                {
                    "narration": "既存TTSへ渡す本文",
                    "dialogue": [{"speaker": "zundamon", "text": "このセリフは使わない"}],
                }
            ]
        },
        dialogue_enabled=False,
    )

    assert [(line.speaker, line.text) for line in lines] == [("zundamon", "既存TTSへ渡す本文")]


def test_character_frames_use_open_mouth_variant_when_available(tmp_path: Path) -> None:
    assets_dir = tmp_path / "characters"
    _write_portrait(assets_dir / "zundamon" / "normal.png", (80, 180, 120))
    _write_portrait(assets_dir / "zundamon" / "talk.png", (110, 230, 150))
    _write_portrait(assets_dir / "metan" / "normal.png", (180, 110, 180))
    background = tmp_path / "background.png"
    Image.new("RGB", (1920, 1080), (20, 30, 50)).save(background)

    lines = extract_speech_lines(
        {
            "sections": [
                {
                    "dialogue": [
                        {"speaker": "zundamon", "text": "こんにちは", "emotion": "neutral"},
                        {"speaker": "metan", "text": "よろしく", "emotion": "neutral"},
                    ]
                }
            ]
        },
        dialogue_enabled=True,
    )
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        CHARACTER_RENDER_ENABLED=True,
        CHARACTER_ASSETS_DIR=str(assets_dir),
    )

    frames = build_scene_frames(
        background=background,
        lines=lines,
        durations=[0.5, 0.5],
        settings=settings,
        output_dir=tmp_path / "frames",
    )

    assert len(frames) > 2
    assert all(frame.path.exists() for frame in frames)
