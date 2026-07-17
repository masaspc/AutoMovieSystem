from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.core.config import get_settings
from app.services.media import renderer

pytestmark = [pytest.mark.media, pytest.mark.usefixtures("ffmpeg_required")]


def _red_marker_bbox(path: Path) -> tuple[int, int, int, int] | None:
    with Image.open(path) as image:
        red_mask = Image.new("1", image.size)
        red_mask.putdata(
            [
                red > 180 and green < 100 and blue < 100
                for red, green, blue in image.convert("RGB").get_flattened_data()
            ]
        )
    return red_mask.getbbox()


def test_ken_burns_moves_background_but_keeps_character_overlay_fixed(tmp_path: Path) -> None:
    background = tmp_path / "background.png"
    background_image = Image.new("RGB", (1920, 1080), (20, 30, 45))
    background_draw = ImageDraw.Draw(background_image)
    for x in range(0, 1920, 80):
        background_draw.line((x, 0, x, 1080), fill=(40 + x % 180, 90, 130), width=8)
    background_image.save(background)

    overlay = tmp_path / "overlay.png"
    overlay_image = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay_image)
    overlay_draw.rectangle((24, 420, 124, 980), fill=(255, 0, 0, 255))
    overlay_draw.rectangle((1796, 420, 1896, 980), fill=(255, 0, 0, 255))
    overlay_image.save(overlay)

    output = tmp_path / "scene.mp4"
    renderer._build_scene_video_track(
        get_settings().resolved_ffmpeg_path,
        [
            renderer.SceneFrame(
                path=background,
                duration_seconds=2.0,
                background_path=background,
                overlay_path=overlay,
            )
        ],
        output,
        timeout=30,
        ken_burns_style="zoom_in_right",
    )

    extracted = []
    for index, timestamp in enumerate((0.1, 1.7)):
        frame_path = tmp_path / f"frame-{index}.png"
        renderer._run_ffmpeg(
            get_settings().resolved_ffmpeg_path,
            ["-y", "-ss", str(timestamp), "-i", str(output), "-frames:v", "1", str(frame_path)],
            timeout=30,
        )
        extracted.append(frame_path)

    assert _red_marker_bbox(extracted[0]) == _red_marker_bbox(extracted[1])
