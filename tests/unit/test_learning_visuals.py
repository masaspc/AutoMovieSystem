from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from app.providers.background.fake import FakeBackgroundProvider
from app.providers.background.pillow import PillowBackgroundProvider
from app.providers.youtube.base import AudienceRetentionPoint
from app.services.analytics.retention import correlate_retention_dips
from app.services.media.visuals import (
    _code_from_section,
    build_background_frames,
    generate_section_visual,
    write_scene_manifest,
)


def test_code_visual_extracts_code_examples_instead_of_drawing_full_instruction() -> None:
    code = _code_from_section(
        {
            "code": "",
            "visual_instruction": (
                "VS Codeに「print(20 + '30')」を表示し、"
                "並列に「print(20 + 30)」を表示する"
            ),
        }
    )

    assert code == "print(20 + '30')\nprint(20 + 30)"
    assert "VS Code" not in code


def test_generate_code_quiz_and_key_point_visuals(tmp_path: Path) -> None:
    sections = [
        {
            "heading": "コード",
            "visual_type": "code",
            "background_style": "editor",
            "code": 'print("hello")',
            "highlight_lines": [1],
            "emphasis_words": ["print", "hello"],
        },
        {
            "heading": "確認問題",
            "visual_type": "quiz",
            "background_style": "quiz",
            "quiz_question": "出力はどれ？",
            "quiz_options": ["hello", "error"],
        },
        {
            "heading": "要点",
            "visual_type": "key_point",
            "visual_bullets": ["一つ目", "二つ目"],
        },
        {
            "heading": "比較",
            "visual_type": "chart",
            "chart_title": "学習時間の比較",
            "chart_labels": ["開始前", "1週間後", "1か月後"],
            "chart_values": [1, 4, 10],
            "emphasis_words": ["継続", "10倍"],
        },
    ]
    for index, section in enumerate(sections):
        path = generate_section_visual(section, tmp_path / f"{index}.png")
        with Image.open(path) as image:
            assert image.size == (1920, 1080)


def test_heading_and_accent_variety_change_generated_visual(tmp_path: Path) -> None:
    section = {"heading": "演出テスト", "visual_type": "key_point"}
    outputs = []
    for index, heading_style in enumerate(("banner", "underline", "side_accent")):
        path = generate_section_visual(
            section,
            tmp_path / f"heading-{index}.png",
            heading_style=heading_style,
            accent_hue_shift=12,
        )
        outputs.append(path.read_bytes())

    assert len(set(outputs)) == 3


def test_background_providers_follow_the_same_contract(tmp_path: Path) -> None:
    section = {"heading": "背景", "visual_type": "key_point"}
    for index, provider in enumerate((PillowBackgroundProvider(), FakeBackgroundProvider())):
        path = provider.generate(section, tmp_path / f"provider-{index}.png")
        with Image.open(path) as image:
            assert image.size == (1920, 1080)


def test_scene_manifest_maps_retention_dip_to_visual_type(tmp_path: Path) -> None:
    path = write_scene_manifest(
        [
            {"heading": "導入", "visual_type": "dialogue"},
            {"heading": "コード", "visual_type": "code"},
        ],
        [0, 1],
        [10.0, 10.0],
        tmp_path / "manifest.json",
    )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    dips = correlate_retention_dips(
        manifest,
        [
            AudienceRetentionPoint(0.25, 0.8, 0.1),
            AudienceRetentionPoint(0.75, 0.3, -0.4),
        ],
    )
    assert len(dips) == 1
    assert dips[0]["visual_type"] == "code"
    assert dips[0]["section_index"] == 1


def test_background_frames_switch_without_characters(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    frames = build_background_frames({0: first, 1: second}, [0, 1], [2.0, 3.0])
    assert [frame.path for frame in frames] == [first, second]
    assert [frame.duration_seconds for frame in frames] == [2.0, 3.0]
