from __future__ import annotations

from app.services.media.subtitles import (
    MAX_CHARS_PER_LINE,
    MAX_LINES_PER_CUE,
    build_cues,
    render_srt,
    render_vtt,
)


def test_build_cues_timestamps_are_proportional_to_section_duration() -> None:
    # 字幕セーフエリア向けの短い行長で複数キューへ分割される。
    sections = [
        {"heading": "導入", "narration": "あ" * 126},
        {"heading": "本編", "narration": "い" * 42},
    ]
    durations = [10.0, 5.0]

    cues = build_cues(sections, section_durations=durations)

    # 126字は26字/行で5行、最大2行/キューなので3キューになる。
    section0_cues = [c for c in cues if c.start_seconds < 10.0]
    assert len(section0_cues) == 3
    assert section0_cues[0].start_seconds == 0.0
    assert abs(section0_cues[0].end_seconds - (10.0 * 52 / 126)) < 0.01
    assert abs(section0_cues[-1].end_seconds - 10.0) < 0.01

    section1_cues = [c for c in cues if c.start_seconds >= 10.0]
    assert len(section1_cues) == 1
    assert section1_cues[0].start_seconds == 10.0
    assert abs(section1_cues[0].end_seconds - 15.0) < 0.01


def test_build_cues_max_two_lines_and_safe_chars_per_line() -> None:
    narration = "あ" * 200
    sections = [{"heading": "本編", "narration": narration}]
    durations = [20.0]

    cues = build_cues(sections, section_durations=durations)

    for cue in cues:
        assert len(cue.lines) <= MAX_LINES_PER_CUE
        for line in cue.lines:
            assert len(line) <= MAX_CHARS_PER_LINE

    # 全キューを結合すると元のテキストに一致する(欠落なし)。
    joined = "".join(line for cue in cues for line in cue.lines)
    assert joined == narration


def test_subtitle_end_time_never_exceeds_total_audio_duration() -> None:
    sections = [
        {"heading": "導入", "narration": "テスト" * 30},
        {"heading": "本編", "narration": "本編内容" * 40},
        {"heading": "まとめ", "narration": "まとめの文章です" * 10},
    ]
    durations = [3.3, 7.7, 2.1]
    total_duration = sum(durations)

    cues = build_cues(sections, section_durations=durations)

    assert cues, "キューが生成されていること"
    for cue in cues:
        assert cue.end_seconds <= total_duration + 1e-6
        assert cue.start_seconds <= cue.end_seconds


def test_build_cues_raises_on_length_mismatch() -> None:
    import pytest

    sections = [{"heading": "a", "narration": "text"}]
    with pytest.raises(ValueError, match="長さが一致していません"):
        build_cues(sections, section_durations=[1.0, 2.0])


def test_render_srt_format() -> None:
    sections = [{"heading": "導入", "narration": "こんにちは世界"}]
    cues = build_cues(sections, section_durations=[2.0])

    srt = render_srt(cues)
    assert srt.startswith("1\n")
    assert "-->" in srt
    assert "," in srt.splitlines()[1]  # SRTはミリ秒区切りにカンマを使う


def test_render_vtt_format() -> None:
    sections = [{"heading": "導入", "narration": "こんにちは世界"}]
    cues = build_cues(sections, section_durations=[2.0])

    vtt = render_vtt(cues)
    assert vtt.startswith("WEBVTT")
    assert "-->" in vtt
    # WebVTTはミリ秒区切りにピリオドを使う(SRTのカンマと異なる)。
    timestamp_line = [line for line in vtt.splitlines() if "-->" in line][0]
    assert "," not in timestamp_line
    assert "." in timestamp_line


def test_build_cues_skips_empty_narration_sections() -> None:
    sections = [
        {"heading": "空", "narration": ""},
        {"heading": "本編", "narration": "内容あり"},
    ]
    durations = [1.0, 5.0]

    cues = build_cues(sections, section_durations=durations)

    assert len(cues) == 1
    assert cues[0].start_seconds == 1.0
