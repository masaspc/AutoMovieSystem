from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.core.config import Settings
from app.services.media.characters import (
    _build_crossfade_frames,
    _portrait_path,
    build_scene_frames,
    character_credits,
)
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
    _write_portrait(assets_dir / "zundamon" / "blink.png", (60, 120, 90))
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
        durations=[4.0, 0.5],
        settings=settings,
        output_dir=tmp_path / "frames",
    )

    assert len(frames) > 2
    assert all(frame.path.exists() for frame in frames)
    assert any("blink" in frame.path.name for frame in frames)


def _region_has_non_background_pixels(
    image: Image.Image, box: tuple[int, int, int, int], background_color: tuple[int, int, int]
) -> bool:
    region = image.crop(box).convert("RGB")
    return region.tobytes() != bytes(background_color) * (region.width * region.height)


def test_characters_stay_visible_at_both_edges_even_with_hidden_layout(tmp_path: Path) -> None:
    """運用フィードバック: キャラクターは途中で消えず、掛け合いの2人は両端に立つ。

    character_layout="hidden" や "small_right" を指定するセクションでも、
    左端(ずんだもん)・右端(めたん)の両方が描画されることを検証する。
    """
    assets_dir = tmp_path / "characters"
    _write_portrait(assets_dir / "zundamon" / "normal.png", (80, 180, 120))
    _write_portrait(assets_dir / "metan" / "normal.png", (180, 110, 180))
    background = tmp_path / "background.png"
    bg_color = (20, 30, 50)
    Image.new("RGB", (1920, 1080), bg_color).save(background)

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        CHARACTER_RENDER_ENABLED=True,
        CHARACTER_ASSETS_DIR=str(assets_dir),
    )

    for layout in ("hidden", "small_right", "full"):
        lines = extract_speech_lines(
            {
                "sections": [
                    {
                        "character_layout": layout,
                        "dialogue": [
                            {"speaker": "zundamon", "text": "こんにちは", "emotion": "neutral"},
                            {"speaker": "metan", "text": "よろしく", "emotion": "neutral"},
                        ],
                    }
                ]
            },
            dialogue_enabled=True,
        )
        frames = build_scene_frames(
            background=background,
            lines=lines,
            durations=[0.2, 0.2],
            settings=settings,
            output_dir=tmp_path / f"frames_{layout}",
            sections=[{"character_layout": layout}],
        )
        with Image.open(frames[0].path) as frame:
            # 左端・右端の下半分にキャラクターのピクセルがある(=消えていない)。
            assert _region_has_non_background_pixels(frame, (0, 540, 450, 1080), bg_color), (
                f"layout={layout}: 左端にキャラクターが描画されていません"
            )
            assert _region_has_non_background_pixels(frame, (1470, 540, 1920, 1080), bg_color), (
                f"layout={layout}: 右端にキャラクターが描画されていません"
            )
            # 中央帯(コード表示等が入る領域)は空けたまま。
            assert not _region_has_non_background_pixels(frame, (800, 300, 1120, 700), bg_color), (
                f"layout={layout}: 中央領域にキャラクターがはみ出しています"
            )


def test_open_and_closed_frames_keep_identical_anchor_no_vertical_motion(tmp_path: Path) -> None:
    """運用フィードバック: 立ち絵は上下に動かさない(口パク差分でもY位置は不変)。"""
    assets_dir = tmp_path / "characters"
    _write_portrait(assets_dir / "zundamon" / "normal.png", (80, 180, 120))
    _write_portrait(assets_dir / "zundamon" / "talk.png", (80, 180, 120))  # 同色・同サイズの差分
    _write_portrait(assets_dir / "metan" / "normal.png", (180, 110, 180))
    background = tmp_path / "background.png"
    Image.new("RGB", (1920, 1080), (20, 30, 50)).save(background)

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        CHARACTER_RENDER_ENABLED=True,
        CHARACTER_ASSETS_DIR=str(assets_dir),
    )
    lines = extract_speech_lines(
        {
            "sections": [
                {"dialogue": [{"speaker": "zundamon", "text": "こんにちは", "emotion": "neutral"}]}
            ]
        },
        dialogue_enabled=True,
    )
    build_scene_frames(
        background=background,
        lines=lines,
        durations=[2.0],
        settings=settings,
        output_dir=tmp_path / "frames",
    )

    # 口閉じ/口開きの合成結果が完全一致する(=位置ずれ・上下移動が一切ない)。
    # 差分素材は同色・同サイズなので、位置が1pxでも動けば画像は一致しなくなる。
    with (
        Image.open(tmp_path / "frames" / "line_000_closed.png") as closed,
        Image.open(tmp_path / "frames" / "line_000_open.png") as opened,
    ):
        assert closed.tobytes() == opened.tobytes()


def test_optional_pose_asset_is_preferred_and_missing_pose_falls_back(tmp_path: Path) -> None:
    assets_dir = tmp_path / "characters"
    normal = assets_dir / "zundamon" / "normal.png"
    pointing = assets_dir / "zundamon" / "pointing.png"
    _write_portrait(normal, (80, 180, 120))
    _write_portrait(pointing, (220, 80, 80))
    settings = Settings(_env_file=None, CHARACTER_ASSETS_DIR=str(assets_dir))  # type: ignore[call-arg]

    assert (
        _portrait_path(settings, "zundamon", "neutral", talking=False, pose="pointing") == pointing
    )
    assert _portrait_path(settings, "zundamon", "neutral", talking=False, pose="thinking") == normal


def test_section_change_adds_crossfade_without_changing_total_duration(tmp_path: Path) -> None:
    assets_dir = tmp_path / "characters"
    _write_portrait(assets_dir / "zundamon" / "normal.png", (80, 180, 120))
    _write_portrait(assets_dir / "metan" / "normal.png", (180, 110, 180))
    first_bg = tmp_path / "first.png"
    second_bg = tmp_path / "second.png"
    Image.new("RGB", (1920, 1080), (20, 30, 50)).save(first_bg)
    Image.new("RGB", (1920, 1080), (80, 30, 20)).save(second_bg)
    lines = extract_speech_lines(
        {"sections": [{"narration": "最初"}, {"narration": "次"}]},
        dialogue_enabled=False,
    )
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        CHARACTER_RENDER_ENABLED=True,
        CHARACTER_ASSETS_DIR=str(assets_dir),
    )

    frames = build_scene_frames(
        backgrounds={0: first_bg, 1: second_bg},
        lines=lines,
        durations=[1.0, 1.0],
        sections=[{"visual_type": "dialogue"}, {"visual_type": "dialogue"}],
        settings=settings,
        output_dir=tmp_path / "frames",
    )

    assert any("transition_001" in frame.path.name for frame in frames)
    assert abs(sum(frame.duration_seconds for frame in frames) - 2.0) < 1e-6


def test_section_transition_styles_generate_distinct_frames(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    target = tmp_path / "target.png"
    Image.new("RGB", (1920, 1080), (10, 20, 30)).save(source)
    Image.new("RGB", (1920, 1080), (220, 120, 40)).save(target)

    samples = []
    for index, style in enumerate(("fade", "wipeleft", "slideup", "circleopen")):
        output_dir = tmp_path / style
        output_dir.mkdir()
        frames = _build_crossfade_frames(
            source=source,
            target=target,
            output_dir=output_dir,
            section_index=index,
            duration=0.4,
            style=style,
        )
        assert abs(sum(frame.duration_seconds for frame in frames) - 0.4) < 1e-6
        samples.append(frames[1].path.read_bytes())

    assert len(set(samples)) == 4
