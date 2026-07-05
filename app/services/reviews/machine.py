"""機械検査(仕様§12)。ffprobe/ffmpegによる決定的な出力動画品質検査。

すべての判定はfail-closed(判定不能・不明な場合はblocking扱い)。
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.subprocess_util import SubprocessError, run_checked
from app.models.asset import Asset
from app.models.script import Script
from app.models.video_project import VideoProject
from app.services.media.probe import ProbeError, probe_video
from app.services.reviews.findings import SEVERITY_BLOCKING, SEVERITY_WARNING, Finding

logger = get_logger(__name__)

_SILENCE_DURATION_PATTERN = re.compile(r"silence_duration:\s*([\d.]+)")
_MEAN_VOLUME_PATTERN = re.compile(r"mean_volume:\s*(-?[\d.]+)\s*dB")
_SRT_TIMESTAMP_PATTERN = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)

# ffprobe/ffmpeg検査の許容誤差(秒)。字幕終了時刻が動画尺をわずかに超える丸め誤差を吸収する。
_SUBTITLE_TOLERANCE_SECONDS = 0.5


def _parse_srt_last_end_seconds(srt_text: str) -> float:
    """SRTテキストから最終キューの終了タイムスタンプ(秒)を返す。"""
    last = 0.0
    for match in _SRT_TIMESTAMP_PATTERN.finditer(srt_text):
        hours, minutes, seconds, millis = (int(match.group(i)) for i in (5, 6, 7, 8))
        end_seconds = hours * 3600 + minutes * 60 + seconds + millis / 1000
        last = max(last, end_seconds)
    return last


def detect_silence_durations(path: Path) -> list[float]:
    """ffmpeg silencedetectフィルタで無音区間の長さ一覧(秒)を返す(検出失敗時は空)。"""
    settings = get_settings()
    args = [
        settings.resolved_ffmpeg_path,
        "-i",
        str(path),
        "-af",
        f"silencedetect=n={settings.REVIEW_SILENCE_THRESHOLD_DB}dB:"
        f"d={settings.REVIEW_SILENCE_MIN_DURATION_SECONDS}",
        "-f",
        "null",
        "-",
    ]
    try:
        result = run_checked(args, timeout=settings.MEDIA_FFMPEG_TIMEOUT_SECONDS)
    except SubprocessError as exc:
        logger.warning("silencedetect_failed", error=str(exc))
        return []
    return [float(m.group(1)) for m in _SILENCE_DURATION_PATTERN.finditer(result.stderr)]


def detect_mean_volume_db(path: Path) -> float | None:
    """ffmpeg volumedetectフィルタで平均音量(dB)を返す(検出失敗時はNone)。"""
    settings = get_settings()
    args = [
        settings.resolved_ffmpeg_path,
        "-i",
        str(path),
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ]
    try:
        result = run_checked(args, timeout=settings.MEDIA_FFMPEG_TIMEOUT_SECONDS)
    except SubprocessError as exc:
        logger.warning("volumedetect_failed", error=str(exc))
        return None
    match = _MEAN_VOLUME_PATTERN.search(result.stderr)
    return float(match.group(1)) if match else None


def inspect_machine(session: Session, project: VideoProject) -> list[Finding]:
    """レンダリング済み動画をffprobe/ffmpegで検査し、Finding一覧を返す(仕様§12)。"""
    findings: list[Finding] = []
    settings = get_settings()

    if not project.output_path:
        findings.append(Finding("missing_output_path", SEVERITY_BLOCKING, "出力パスが未設定です"))
        return findings

    path = Path(project.output_path)
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

    if project.target_duration_seconds is None:
        findings.append(
            Finding(
                "missing_target_duration", SEVERITY_BLOCKING, "target_duration_secondsが未設定です"
            )
        )
    else:
        min_allowed = project.target_duration_seconds * settings.REVIEW_DURATION_MIN_RATIO
        max_allowed = project.target_duration_seconds * settings.REVIEW_DURATION_MAX_RATIO
        if not (min_allowed <= probe.duration_seconds <= max_allowed):
            findings.append(
                Finding(
                    "duration_out_of_range",
                    SEVERITY_BLOCKING,
                    f"尺が範囲外です: {probe.duration_seconds:.2f}s "
                    f"(期待範囲 {min_allowed:.2f}-{max_allowed:.2f}s)",
                )
            )

    if not probe.has_audio:
        findings.append(
            Finding("missing_audio_track", SEVERITY_BLOCKING, "音声トラックがありません")
        )

    if probe.width < settings.REVIEW_MIN_WIDTH or probe.height < settings.REVIEW_MIN_HEIGHT:
        findings.append(
            Finding(
                "resolution_too_small",
                SEVERITY_BLOCKING,
                f"解像度が不足しています: {probe.width}x{probe.height} "
                f"(最小 {settings.REVIEW_MIN_WIDTH}x{settings.REVIEW_MIN_HEIGHT})",
            )
        )

    if probe.has_audio:
        silence_durations = detect_silence_durations(path)
        if silence_durations:
            findings.append(
                Finding(
                    "long_silence_detected",
                    SEVERITY_BLOCKING,
                    f"長時間無音を検出しました(最大{max(silence_durations):.1f}秒)",
                )
            )

        mean_volume = detect_mean_volume_db(path)
        if mean_volume is not None and not (
            settings.REVIEW_VOLUME_MIN_DB <= mean_volume <= settings.REVIEW_VOLUME_MAX_DB
        ):
            findings.append(
                Finding(
                    "volume_out_of_range",
                    SEVERITY_WARNING,
                    f"平均音量が範囲外です: {mean_volume:.1f}dB (期待範囲 "
                    f"{settings.REVIEW_VOLUME_MIN_DB}〜{settings.REVIEW_VOLUME_MAX_DB}dB)",
                )
            )

    subtitle_assets = (
        session.query(Asset)
        .filter(Asset.video_project_id == project.id, Asset.asset_type == "subtitle")
        .all()
    )
    srt_asset = next((a for a in subtitle_assets if (a.meta or {}).get("kind") == "srt"), None)
    if srt_asset is None:
        findings.append(
            Finding("missing_subtitle_asset", SEVERITY_BLOCKING, "字幕Assetが存在しません")
        )
    else:
        srt_path = Path(srt_asset.file_path)
        if not srt_path.exists():
            findings.append(
                Finding(
                    "missing_subtitle_file",
                    SEVERITY_BLOCKING,
                    f"字幕ファイルが存在しません: {srt_path}",
                )
            )
        else:
            last_end = _parse_srt_last_end_seconds(srt_path.read_text(encoding="utf-8"))
            if last_end > probe.duration_seconds + _SUBTITLE_TOLERANCE_SECONDS:
                findings.append(
                    Finding(
                        "subtitle_exceeds_video_duration",
                        SEVERITY_BLOCKING,
                        f"字幕の最終タイムスタンプ({last_end:.2f}s)が"
                        f"動画尺({probe.duration_seconds:.2f}s)を超えています",
                    )
                )

    script = session.get(Script, project.script_id) if project.script_id else None
    if script is None:
        findings.append(Finding("missing_script", SEVERITY_BLOCKING, "Scriptが紐づいていません"))
    else:
        if not script.title:
            findings.append(
                Finding("missing_script_title", SEVERITY_BLOCKING, "Script titleが空です")
            )
        if not (script.body or {}).get("description"):
            findings.append(
                Finding(
                    "missing_script_description", SEVERITY_BLOCKING, "Script descriptionが空です"
                )
            )

    if project.checksum:
        duplicate = (
            session.query(VideoProject)
            .filter(VideoProject.checksum == project.checksum, VideoProject.id != project.id)
            .first()
        )
        if duplicate is not None:
            findings.append(
                Finding(
                    "duplicate_output_checksum",
                    SEVERITY_BLOCKING,
                    f"他のVideoProject({duplicate.id})と出力checksumが一致しています(重複動画)",
                )
            )

    return findings
