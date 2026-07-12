"""字幕(SRT/WebVTT)生成(仕様§10)。

Script構造化body(`sections[].narration`)+各セクションの音声尺から、
1画面あたり最大2行・42字/行に分割したキューを組み立て、SRT/WebVTTへ整形する。
タイムスタンプはセクション音声尺で文字数比例配分し、字幕終了時間が
音声全体の尺を超えないようクランプする。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

MAX_CHARS_PER_LINE = 26
MAX_LINES_PER_CUE = 2


@dataclass(frozen=True)
class SubtitleCue:
    index: int
    start_seconds: float
    end_seconds: float
    lines: list[str]


def _wrap_text(text: str, *, max_chars: int = MAX_CHARS_PER_LINE) -> list[str]:
    """textを1行max_chars文字で折り返す(単純な文字数分割)。"""
    stripped = text.strip()
    if not stripped:
        return []
    return [stripped[i : i + max_chars] for i in range(0, len(stripped), max_chars)]


def _group_lines_into_cues(
    lines: list[str], *, max_lines: int = MAX_LINES_PER_CUE
) -> list[list[str]]:
    return [lines[i : i + max_lines] for i in range(0, len(lines), max_lines)]


def build_cues_for_section(
    narration: str,
    *,
    section_start: float,
    section_duration: float,
    start_index: int,
) -> list[SubtitleCue]:
    """1セクションのnarrationを複数キューに分割し、セクション尺で比例配分する。"""
    lines = _wrap_text(narration)
    if not lines:
        return []

    grouped = _group_lines_into_cues(lines)
    total_chars = sum(len(line) for group in grouped for line in group) or 1

    cues: list[SubtitleCue] = []
    elapsed = 0.0
    for offset, group in enumerate(grouped):
        group_chars = sum(len(line) for line in group)
        portion = (group_chars / total_chars) * section_duration
        cue_start = section_start + elapsed
        cue_end = min(section_start + section_duration, cue_start + portion)
        cues.append(
            SubtitleCue(
                index=start_index + offset,
                start_seconds=cue_start,
                end_seconds=cue_end,
                lines=group,
            )
        )
        elapsed += portion

    return cues


def build_cues(sections: list[dict], *, section_durations: list[float]) -> list[SubtitleCue]:
    """Scriptのsections(§10)+各セクション音声尺からキュー全体を組み立てる。

    字幕終了時間が音声全体の尺(sum(section_durations))を超えないことを保証する。
    """
    if len(sections) != len(section_durations):
        raise ValueError("sectionsとsection_durationsの長さが一致していません")

    cues: list[SubtitleCue] = []
    section_start = 0.0
    next_index = 1
    for section, duration in zip(sections, section_durations, strict=True):
        narration = section.get("narration", "")
        section_cues = build_cues_for_section(
            narration,
            section_start=section_start,
            section_duration=duration,
            start_index=next_index,
        )
        cues.extend(section_cues)
        next_index += len(section_cues)
        section_start += duration

    total_duration = sum(section_durations)
    clamped_cues = [replace(cue, end_seconds=min(cue.end_seconds, total_duration)) for cue in cues]
    return clamped_cues


def _format_srt_timestamp(seconds: float) -> str:
    total_ms = int(max(0.0, seconds) * 1000)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _format_vtt_timestamp(seconds: float) -> str:
    total_ms = int(max(0.0, seconds) * 1000)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def render_srt(cues: list[SubtitleCue]) -> str:
    """キュー列をSRT形式の文字列に整形する。"""
    blocks = []
    for cue in cues:
        text = "\n".join(cue.lines)
        blocks.append(
            f"{cue.index}\n"
            f"{_format_srt_timestamp(cue.start_seconds)} --> "
            f"{_format_srt_timestamp(cue.end_seconds)}\n"
            f"{text}\n"
        )
    return "\n".join(blocks)


def render_vtt(cues: list[SubtitleCue]) -> str:
    """キュー列をWebVTT形式の文字列に整形する。"""
    blocks = ["WEBVTT", ""]
    for cue in cues:
        text = "\n".join(cue.lines)
        blocks.append(
            f"{_format_vtt_timestamp(cue.start_seconds)} --> "
            f"{_format_vtt_timestamp(cue.end_seconds)}\n{text}"
        )
    return "\n\n".join(blocks) + "\n"
