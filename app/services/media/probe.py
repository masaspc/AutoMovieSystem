"""ffprobeによる出力動画検査。

レンダリング成果物は必ず本モジュールの `probe_video`/`inspect_rendered_video` で
検証してから成功と報告する(コーデック・尺・解像度・音声有無)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from app.core.config import get_settings
from app.core.subprocess_util import SubprocessError, run_checked

SEVERITY_BLOCKING = "blocking"
SEVERITY_WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str


@dataclass(frozen=True)
class VideoProbeResult:
    """ffprobeの出力から抽出した動画メタ情報。"""

    duration_seconds: float
    width: int
    height: int
    fps: float
    video_codec: str | None
    audio_codec: str | None
    has_audio: bool
    size_bytes: int


class ProbeError(RuntimeError):
    """ffprobe実行・パース失敗。"""


def _parse_fps(rate: str | None) -> float:
    if not rate:
        return 0.0
    try:
        return float(Fraction(rate))
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe_video(path: Path) -> VideoProbeResult:
    """`ffprobe -print_format json -show_format -show_streams` で動画メタ情報を取得する。"""
    if not path.exists():
        raise ProbeError(f"ファイルが存在しません: {path}")

    settings = get_settings()
    args = [
        settings.resolved_ffprobe_path,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]

    try:
        result = run_checked(args, timeout=settings.MEDIA_FFPROBE_TIMEOUT_SECONDS)
    except SubprocessError as exc:
        raise ProbeError(f"ffprobe実行に失敗しました: {exc}") from exc

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe出力のJSONパースに失敗しました: {exc}") from exc

    streams = payload.get("streams", [])
    fmt = payload.get("format", {})

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration_str = fmt.get("duration") or (video_stream or {}).get("duration")
    duration_seconds = float(duration_str) if duration_str is not None else 0.0

    width = int((video_stream or {}).get("width", 0))
    height = int((video_stream or {}).get("height", 0))
    fps = _parse_fps((video_stream or {}).get("r_frame_rate")) if video_stream else 0.0
    video_codec = (video_stream or {}).get("codec_name")
    audio_codec = (audio_stream or {}).get("codec_name")
    size_bytes = int(fmt.get("size", 0)) if fmt.get("size") is not None else path.stat().st_size

    return VideoProbeResult(
        duration_seconds=duration_seconds,
        width=width,
        height=height,
        fps=fps,
        video_codec=video_codec,
        audio_codec=audio_codec,
        has_audio=audio_stream is not None,
        size_bytes=size_bytes,
    )


def inspect_rendered_video(
    path: Path,
    *,
    expected_min_duration: float,
    expected_max_duration: float,
    min_width: int = 1,
    min_height: int = 1,
    require_audio: bool = True,
) -> list[Finding]:
    """レンダリング済み動画をffprobeで検査し、Finding一覧を返す(blockingがあれば不合格)。"""
    findings: list[Finding] = []

    if not path.exists() or path.stat().st_size == 0:
        findings.append(
            Finding("empty_or_missing_file", SEVERITY_BLOCKING, f"ファイルが空/不在です: {path}")
        )
        return findings

    try:
        probe = probe_video(path)
    except ProbeError as exc:
        findings.append(Finding("unreadable_video", SEVERITY_BLOCKING, str(exc)))
        return findings

    if not (expected_min_duration <= probe.duration_seconds <= expected_max_duration):
        findings.append(
            Finding(
                "duration_out_of_range",
                SEVERITY_BLOCKING,
                f"尺が範囲外です: {probe.duration_seconds:.2f}s "
                f"(期待範囲 {expected_min_duration:.2f}-{expected_max_duration:.2f}s)",
            )
        )

    if require_audio and not probe.has_audio:
        findings.append(
            Finding("missing_audio_track", SEVERITY_BLOCKING, "音声トラックがありません")
        )

    if probe.width < min_width or probe.height < min_height:
        findings.append(
            Finding(
                "resolution_too_small",
                SEVERITY_BLOCKING,
                f"解像度が不足しています: {probe.width}x{probe.height} "
                f"(最小 {min_width}x{min_height})",
            )
        )

    if probe.video_codec is None:
        findings.append(
            Finding("missing_video_codec", SEVERITY_BLOCKING, "映像コーデックが不明です")
        )

    return findings
