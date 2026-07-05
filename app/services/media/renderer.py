"""FFmpegレンダリングパイプライン(仕様 Phase 3)。

FFmpegコマンドは必ず引数配列で構築し `shell=False`(`app/core/subprocess_util.py`)で
実行する。出力は `generated/` 配下のみ(D-008)。

冪等性: 呼び出し側が算出した決定的な `input_checksum`(script checksum+設定等)から
出力ファイル名を導出し、既存ファイル+サイドカーの `input_checksum` が一致すれば
再レンダリングしない。

9:16(縦動画)は解像度・pad計算をaspect_ratio引数で分岐できる構造のみ用意し、
実装は16:9のみ(9:16は `NotImplementedError`)。
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

import structlog
from PIL import Image, ImageDraw, ImageFont

from app.core.config import Settings, get_settings
from app.core.paths import resolve_generated_path
from app.core.subprocess_util import SubprocessError, run_checked
from app.services.media.probe import probe_video

logger = structlog.get_logger(__name__)

VIDEO_WIDTH_16_9 = 1920
VIDEO_HEIGHT_16_9 = 1080
VIDEO_FPS = 30
AUDIO_SAMPLE_RATE = 48_000
DEFAULT_ENDCARD_DURATION_SECONDS = 3.0

SUPPORTED_ASPECT_RATIOS = ("16:9",)

# 日本語表示可能なフォント候補(見つからない場合はPillowのデフォルトフォントにフォールバック)。
_JAPANESE_FONT_CANDIDATES = (
    "C:/Windows/Fonts/YuGothR.ttc",
    "C:/Windows/Fonts/meiryo.ttc",
    "C:/Windows/Fonts/msgothic.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-JP-Regular.otf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
)


class RenderError(RuntimeError):
    """レンダリング失敗(FFmpeg実行失敗・入力不正等)。"""


@dataclass(frozen=True)
class RenderInputs:
    """`render_video` への入力一式。"""

    video_project_id: str
    aspect_ratio: str
    section_audio_paths: list[Path]
    background_image_path: Path
    subtitle_srt_path: Path
    title: str
    channel_name: str
    # 呼び出し側が算出する決定的な入力ハッシュ(script checksum+レンダリング設定等)。
    input_checksum: str
    endcard_enabled: bool = True
    endcard_duration_seconds: float = DEFAULT_ENDCARD_DURATION_SECONDS


@dataclass(frozen=True)
class RenderResult:
    output_path: Path
    checksum: str
    duration_seconds: float
    skipped: bool


def compute_file_checksum(path: Path) -> str:
    """ファイルのSHA256ハイダイジェストを計算する(冪等判定・パイプライン間で共有する公開関数)。"""
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _escape_subtitles_filter_path(path: Path) -> str:
    """`subtitles` フィルタ引数用にパスをエスケープする(Windowsのドライブコロン対応)。"""
    normalized = str(path.resolve()).replace("\\", "/")
    normalized = normalized.replace(":", r"\:")
    normalized = normalized.replace("'", r"\'")
    return normalized


def _find_japanese_font() -> str | None:
    for candidate in _JAPANESE_FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def generate_background_image(
    output_path: Path,
    *,
    width: int = VIDEO_WIDTH_16_9,
    height: int = VIDEO_HEIGHT_16_9,
    color: tuple[int, int, int] = (24, 28, 46),
) -> Path:
    """デフォルトの単色背景画像を生成する(Pillow)。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (width, height), color=color)
    draw = ImageDraw.Draw(image)
    draw.rectangle([40, 40, width - 40, height - 40], outline=(90, 100, 140), width=6)
    image.save(output_path, format="PNG")
    return output_path


def generate_endcard_image(
    output_path: Path,
    *,
    title: str,
    channel_name: str,
    width: int = VIDEO_WIDTH_16_9,
    height: int = VIDEO_HEIGHT_16_9,
) -> Path:
    """エンドカード静止画(タイトル+チャンネル名)を生成する。

    日本語フォントが見つからない環境(CI Linux等)ではデフォルトフォントに
    フォールバックし、描画に失敗してもエンドカード自体の生成は継続する。
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (width, height), color=(15, 15, 25))
    draw = ImageDraw.Draw(image)

    font_path = _find_japanese_font()
    try:
        title_font = ImageFont.truetype(font_path, 64) if font_path else ImageFont.load_default()
        channel_font = ImageFont.truetype(font_path, 36) if font_path else ImageFont.load_default()
    except OSError:
        title_font = ImageFont.load_default()
        channel_font = ImageFont.load_default()

    try:
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_w = title_bbox[2] - title_bbox[0]
        draw.text(
            ((width - title_w) / 2, height / 2 - 80), title, fill=(240, 240, 240), font=title_font
        )

        channel_bbox = draw.textbbox((0, 0), channel_name, font=channel_font)
        channel_w = channel_bbox[2] - channel_bbox[0]
        draw.text(
            ((width - channel_w) / 2, height / 2 + 20),
            channel_name,
            fill=(180, 180, 200),
            font=channel_font,
        )
    except Exception as exc:  # noqa: BLE001 - フォント未対応文字等で描画失敗しても生成は継続する
        logger.debug("background_text_draw_skipped", error=str(exc))

    image.save(output_path, format="PNG")
    return output_path


def output_relative_path(video_project_id: str, input_checksum: str) -> str:
    """出力MP4の `GENERATED_DIR` からの相対パス(決定的な命名規則)。"""
    return f"videos/{video_project_id}/video_{input_checksum[:16]}.mp4"


def sidecar_checksum_path(output_path: Path) -> Path:
    """出力に対応するサイドカー(入力チェックサム記録用)ファイルパス。"""
    return output_path.parent / f"{output_path.name}.input_checksum"


def _run_ffmpeg(ffmpeg_path: str, args: list[str], *, timeout: float) -> None:
    try:
        run_checked([ffmpeg_path, *args], timeout=timeout)
    except SubprocessError as exc:
        raise RenderError(f"FFmpeg実行に失敗しました: {exc}") from exc


def _build_audio_track(
    ffmpeg_path: str, section_audio_paths: list[Path], output: Path, *, timeout: float
) -> None:
    """セクション音声を連結し loudnorm(I=-16) で正規化、AAC 48kHzへエンコードする。"""
    input_args: list[str] = []
    for p in section_audio_paths:
        input_args += ["-i", str(p)]

    n = len(section_audio_paths)
    concat_inputs = "".join(f"[{i}:a]" for i in range(n))
    filter_complex = (
        f"{concat_inputs}concat=n={n}:v=0:a=1[concatenated];"
        f"[concatenated]loudnorm=I=-16:TP=-1.5:LRA=11,aresample={AUDIO_SAMPLE_RATE}[outa]"
    )
    args = [
        "-y",
        *input_args,
        "-filter_complex",
        filter_complex,
        "-map",
        "[outa]",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(output),
    ]
    _run_ffmpeg(ffmpeg_path, args, timeout=timeout)


def _build_video_track(
    ffmpeg_path: str,
    background_image: Path,
    output: Path,
    *,
    duration_seconds: float,
    timeout: float,
) -> None:
    """背景画像+軽いズーム(zoompan)で16:9/30fpsの映像トラックを生成する。"""
    vf = (
        f"scale={VIDEO_WIDTH_16_9}:{VIDEO_HEIGHT_16_9}:force_original_aspect_ratio=decrease,"
        f"pad={VIDEO_WIDTH_16_9}:{VIDEO_HEIGHT_16_9}:(ow-iw)/2:(oh-ih)/2,"
        f"zoompan=z='min(zoom+0.0010,1.3)':d=1:s={VIDEO_WIDTH_16_9}x{VIDEO_HEIGHT_16_9},"
        f"fps={VIDEO_FPS}"
    )
    args = [
        "-y",
        "-loop",
        "1",
        "-i",
        str(background_image),
        "-vf",
        vf,
        "-t",
        f"{duration_seconds:.3f}",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        "-an",
        str(output),
    ]
    _run_ffmpeg(ffmpeg_path, args, timeout=timeout)


def _mux_with_subtitles(
    ffmpeg_path: str,
    video_path: Path,
    audio_path: Path,
    subtitle_path: Path,
    output: Path,
    *,
    timeout: float,
) -> None:
    """映像+音声を合成し、字幕を焼き込んでH.264+faststartでMP4出力する。"""
    escaped_subtitle = _escape_subtitles_filter_path(subtitle_path)
    vf = f"subtitles='{escaped_subtitle}'"
    args = [
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output),
    ]
    _run_ffmpeg(ffmpeg_path, args, timeout=timeout)


def _build_endcard_track(
    ffmpeg_path: str,
    endcard_image: Path,
    output: Path,
    *,
    duration_seconds: float,
    timeout: float,
) -> None:
    """エンドカード静止画+無音音声からN秒の動画セグメントを生成する。"""
    vf = (
        f"scale={VIDEO_WIDTH_16_9}:{VIDEO_HEIGHT_16_9}:force_original_aspect_ratio=decrease,"
        f"pad={VIDEO_WIDTH_16_9}:{VIDEO_HEIGHT_16_9}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={VIDEO_FPS}"
    )
    args = [
        "-y",
        "-loop",
        "1",
        "-i",
        str(endcard_image),
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r={AUDIO_SAMPLE_RATE}:cl=mono",
        "-t",
        f"{duration_seconds:.3f}",
        "-vf",
        vf,
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-shortest",
        str(output),
    ]
    _run_ffmpeg(ffmpeg_path, args, timeout=timeout)


def _concat_final(
    ffmpeg_path: str, main_path: Path, endcard_path: Path, output: Path, *, timeout: float
) -> None:
    """本編+エンドカードをconcatフィルタ(再エンコード)で連結する。"""
    args = [
        "-y",
        "-i",
        str(main_path),
        "-i",
        str(endcard_path),
        "-filter_complex",
        "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[outv][outa]",
        "-map",
        "[outv]",
        "-map",
        "[outa]",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(output),
    ]
    _run_ffmpeg(ffmpeg_path, args, timeout=timeout)


def _try_reuse_existing_output(
    output_path: Path, sidecar_path: Path, input_checksum: str
) -> RenderResult | None:
    """既存出力が入力チェックサムと一致し、probe検証も通れば再利用する(冪等スキップ)。"""
    if not (output_path.exists() and sidecar_path.exists()):
        return None

    existing_checksum = sidecar_path.read_text(encoding="utf-8").strip()
    if existing_checksum != input_checksum:
        return None

    try:
        probe_result = probe_video(output_path)
    except Exception:  # noqa: BLE001 - 破損ファイルは再レンダリングにフォールバック
        return None

    checksum = compute_file_checksum(output_path)
    return RenderResult(
        output_path=output_path,
        checksum=checksum,
        duration_seconds=probe_result.duration_seconds,
        skipped=True,
    )


def _render_impl(
    inputs: RenderInputs, settings: Settings, output_path: Path, work_dir: Path
) -> RenderResult:
    ffmpeg_path = settings.resolved_ffmpeg_path
    timeout = settings.MEDIA_FFMPEG_TIMEOUT_SECONDS

    audio_full = work_dir / "audio_full.m4a"
    _build_audio_track(ffmpeg_path, inputs.section_audio_paths, audio_full, timeout=timeout)
    audio_duration = probe_video(audio_full).duration_seconds

    video_main = work_dir / "video_main.mp4"
    _build_video_track(
        ffmpeg_path,
        inputs.background_image_path,
        video_main,
        duration_seconds=audio_duration,
        timeout=timeout,
    )

    muxed_main = work_dir / "muxed_main.mp4"
    _mux_with_subtitles(
        ffmpeg_path, video_main, audio_full, inputs.subtitle_srt_path, muxed_main, timeout=timeout
    )

    if inputs.endcard_enabled:
        endcard_image = work_dir / "endcard.png"
        generate_endcard_image(endcard_image, title=inputs.title, channel_name=inputs.channel_name)
        endcard_video = work_dir / "endcard.mp4"
        _build_endcard_track(
            ffmpeg_path,
            endcard_image,
            endcard_video,
            duration_seconds=inputs.endcard_duration_seconds,
            timeout=timeout,
        )
        final_tmp = work_dir / "final_tmp.mp4"
        _concat_final(ffmpeg_path, muxed_main, endcard_video, final_tmp, timeout=timeout)
    else:
        final_tmp = muxed_main

    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(final_tmp, output_path)

    checksum = compute_file_checksum(output_path)
    duration = probe_video(output_path).duration_seconds
    return RenderResult(
        output_path=output_path, checksum=checksum, duration_seconds=duration, skipped=False
    )


def render_video(inputs: RenderInputs, *, settings: Settings | None = None) -> RenderResult:
    """FFmpegパイプラインで動画をレンダリングする(冪等)。

    Raises:
        NotImplementedError: `aspect_ratio` が16:9以外の場合(9:16は未実装)。
        ValueError: `section_audio_paths` が空の場合。
        RenderError: FFmpeg実行が失敗した場合。
    """
    if inputs.aspect_ratio not in SUPPORTED_ASPECT_RATIOS:
        raise NotImplementedError(
            f"aspect_ratio={inputs.aspect_ratio!r} は未対応です(現状 16:9 のみ実装)"
        )
    if not inputs.section_audio_paths:
        raise ValueError("section_audio_pathsは空にできません")

    settings = settings or get_settings()
    output_path = resolve_generated_path(
        output_relative_path(inputs.video_project_id, inputs.input_checksum)
    )
    sidecar_path = sidecar_checksum_path(output_path)

    reused = _try_reuse_existing_output(output_path, sidecar_path, inputs.input_checksum)
    if reused is not None:
        return reused

    work_dir = output_path.parent / f"work_{output_path.stem}"
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = _render_impl(inputs, settings, output_path, work_dir)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    sidecar_path.write_text(inputs.input_checksum, encoding="utf-8")
    return result
