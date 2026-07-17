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
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from PIL import Image, ImageDraw, ImageFont

from app.core.config import Settings, get_settings
from app.core.paths import resolve_generated_path
from app.core.subprocess_util import SubprocessError, run_checked
from app.services.media.probe import probe_video
from app.services.media.variety import (
    VisualVarietyPlan,
    ken_burns_filter,
    pick_visual_variety_plan,
)

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
    scene_frames: list[SceneFrame] = field(default_factory=list)
    # BGM・SEのミックス指定(Phase A: 音響)。Noneなら従来どおり声のみ。
    audio_mix: AudioMixSpec | None = None
    visual_variety_plan: VisualVarietyPlan | None = None
    disclaimer_text: str = ""


@dataclass(frozen=True)
class SceneFrame:
    """立ち絵演出用の静止フレームと表示秒数。"""

    path: Path
    duration_seconds: float
    # 指定時は背景だけへKen Burnsを適用し、その後に透明前景を固定overlayする。
    background_path: Path | None = None
    overlay_path: Path | None = None


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


def find_japanese_font() -> str | None:
    """利用可能な日本語フォントのパスを返す。"""
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
    disclaimer_text: str = "",
    width: int = VIDEO_WIDTH_16_9,
    height: int = VIDEO_HEIGHT_16_9,
) -> Path:
    """エンドカード静止画(タイトル+チャンネル名+任意の免責文)を生成する。

    日本語フォントが見つからない環境(CI Linux等)ではデフォルトフォントに
    フォールバックし、描画に失敗してもエンドカード自体の生成は継続する。
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (width, height), color=(15, 15, 25))
    draw = ImageDraw.Draw(image)

    font_path = find_japanese_font()
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

        disclaimer = disclaimer_text.strip()
        if disclaimer:
            disclaimer_font = (
                ImageFont.truetype(font_path, 26) if font_path else ImageFont.load_default()
            )
            max_width = width - 240
            lines: list[str] = []
            current = ""
            for character in disclaimer:
                candidate = current + character
                bbox = draw.textbbox((0, 0), candidate, font=disclaimer_font)
                if current and bbox[2] - bbox[0] > max_width:
                    lines.append(current)
                    current = character
                else:
                    current = candidate
            if current:
                lines.append(current)
            lines = lines[:2]
            if len(lines) == 2 and "".join(lines) != disclaimer:
                lines[1] = lines[1].rstrip("…") + "…"
            line_height = 36
            start_y = height - 70 - line_height * len(lines)
            for index, line in enumerate(lines):
                bbox = draw.textbbox((0, 0), line, font=disclaimer_font)
                line_width = bbox[2] - bbox[0]
                draw.text(
                    ((width - line_width) / 2, start_y + index * line_height),
                    line,
                    fill=(165, 165, 180),
                    font=disclaimer_font,
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


@dataclass(frozen=True)
class AudioMixSpec:
    """BGM・SEのミックス指定(Phase A: 音響)。Noneまたは空なら従来どおり声のみ。"""

    bgm_path: Path | None = None
    bgm_volume_db: float = -19.0
    # (SEファイル, 挿入位置秒, 音量dB) のリスト。
    effects: list[tuple[Path, float, float]] = field(default_factory=list)


def build_audio_mix_args(
    section_audio_paths: list[Path],
    output: Path,
    *,
    mix: AudioMixSpec | None = None,
) -> list[str]:
    """音声トラック構築のffmpeg引数を組み立てる(テスト可能な純関数)。

    構成: セリフ連結 → (BGMがあれば)ループBGMをセリフをキーにサイドチェイン圧縮
    (ダッキング=セリフ中はBGMが自動で下がる)してミックス → (SEがあれば)adelayで
    指定位置に重ねる → loudnorm正規化 → AAC 48kHz。
    ミックスは duration=first(セリフ長基準)で、BGMループがセリフ長を超えて
    伸びることはない。
    """
    mix = mix or AudioMixSpec()
    input_args: list[str] = []
    for p in section_audio_paths:
        input_args += ["-i", str(p)]

    n = len(section_audio_paths)
    # concatフィルター直結だと、入力WAVのチャンネルレイアウトが推測値のままとなり、
    # 後段フィルターとの境界でネゴシエーションに失敗することがある
    # (「Cannot select channel layout for the link between filters」)。
    # 連結前に各入力を明示的にmonoへ揃えて回避する。
    formatted_inputs = "".join(f"[{i}:a]aformat=channel_layouts=mono[a{i}];" for i in range(n))
    concat_inputs = "".join(f"[a{i}]" for i in range(n))
    filters = [
        f"{formatted_inputs}{concat_inputs}concat=n={n}:v=0:a=1[voice]",
    ]
    current = "[voice]"
    next_input = n

    if mix.bgm_path is not None:
        input_args += ["-stream_loop", "-1", "-i", str(mix.bgm_path)]
        bgm_index = next_input
        next_input += 1
        filters.append(
            f"[{bgm_index}:a]aformat=channel_layouts=mono,aresample={AUDIO_SAMPLE_RATE},"
            f"volume={mix.bgm_volume_db:.1f}dB[bgm]"
        )
        # セリフをキーにBGMを圧縮(ダッキング)。セリフ側は2分岐して片方をキーに使う。
        filters.append(f"{current}asplit=2[voice_mix][voice_key]")
        filters.append(
            "[bgm][voice_key]sidechaincompress="
            "threshold=0.03:ratio=12:attack=25:release=350[bgm_ducked]"
        )
        filters.append(
            "[voice_mix][bgm_ducked]amix=inputs=2:duration=first:"
            "dropout_transition=0:normalize=0[with_bgm]"
        )
        current = "[with_bgm]"

    if mix.effects:
        se_labels: list[str] = []
        for effect_index, (se_path, offset_seconds, volume_db) in enumerate(mix.effects):
            input_args += ["-i", str(se_path)]
            se_input = next_input
            next_input += 1
            delay_ms = max(0, round(offset_seconds * 1000))
            filters.append(
                f"[{se_input}:a]aformat=channel_layouts=mono,aresample={AUDIO_SAMPLE_RATE},"
                f"volume={volume_db:.1f}dB,adelay={delay_ms}:all=1[se{effect_index}]"
            )
            se_labels.append(f"[se{effect_index}]")
        filters.append(
            f"{current}{''.join(se_labels)}amix=inputs={1 + len(se_labels)}:duration=first:"
            f"dropout_transition=0:normalize=0[with_se]"
        )
        current = "[with_se]"

    # loudnorm通過後も出力のチャンネルレイアウトが未確定のままとなり、最終aresampleで
    # ネゴシエーション失敗が起きるため、直後に明示指定を挟む。
    filters.append(
        f"{current}loudnorm=I=-16:TP=-1.5:LRA=11,"
        f"aformat=channel_layouts=mono,aresample={AUDIO_SAMPLE_RATE}[outa]"
    )

    return [
        "-y",
        *input_args,
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[outa]",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(output),
    ]


def _build_audio_track(
    ffmpeg_path: str,
    section_audio_paths: list[Path],
    output: Path,
    *,
    timeout: float,
    mix: AudioMixSpec | None = None,
) -> None:
    """セリフ連結+BGM/SEミックス+loudnorm正規化でAAC 48kHzの音声トラックを作る。"""
    args = build_audio_mix_args(section_audio_paths, output, mix=mix)
    _run_ffmpeg(ffmpeg_path, args, timeout=timeout)


def _build_video_track(
    ffmpeg_path: str,
    background_image: Path,
    output: Path,
    *,
    duration_seconds: float,
    timeout: float,
    ken_burns_style: str = "zoom_in_left",
) -> None:
    """背景画像+決定論的Ken Burns演出で16:9/30fpsの映像トラックを生成する。"""
    vf = (
        f"scale={VIDEO_WIDTH_16_9}:{VIDEO_HEIGHT_16_9}:force_original_aspect_ratio=decrease,"
        f"pad={VIDEO_WIDTH_16_9}:{VIDEO_HEIGHT_16_9}:(ow-iw)/2:(oh-ih)/2,"
        f"{ken_burns_filter(ken_burns_style)},"
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


def _build_scene_video_track(
    ffmpeg_path: str,
    scene_frames: list[SceneFrame],
    output: Path,
    *,
    timeout: float,
    ken_burns_style: str = "zoom_in_left",
) -> None:
    """concat demuxerで立ち絵フレーム列を連結し、映像トラックを作る。"""
    if not scene_frames:
        raise RenderError("scene_framesは空にできません")

    valid_frames = [frame for frame in scene_frames if frame.duration_seconds > 0]
    if not valid_frames:
        raise RenderError("有効なscene_framesがありません")

    def write_concat_list(path: Path, frame_paths: list[Path]) -> None:
        concat_lines = ["ffconcat version 1.0"]
        last_escaped_path = ""
        for frame, frame_path in zip(valid_frames, frame_paths, strict=True):
            normalized_path = str(frame_path.resolve()).replace("\\", "/")
            escaped_path = normalized_path.replace("'", r"'\''")
            concat_lines.append(f"file '{escaped_path}'")
            concat_lines.append(f"duration {frame.duration_seconds:.6f}")
            last_escaped_path = escaped_path
        # concat demuxerの最終duration解釈差を吸収し、-tで正確な合計尺へクランプする。
        concat_lines.append(f"file '{last_escaped_path}'")
        path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

    concat_list = output.parent / "scene_frames.ffconcat"
    write_concat_list(concat_list, [frame.path for frame in valid_frames])

    total_duration = sum(frame.duration_seconds for frame in valid_frames)
    layered = all(
        frame.background_path is not None and frame.overlay_path is not None
        for frame in valid_frames
    )
    input_args = ["-f", "concat", "-safe", "0", "-i", str(concat_list)]
    filter_args = [
        "-vf",
        f"fps={VIDEO_FPS},{ken_burns_filter(ken_burns_style)},format=yuv420p",
    ]
    if layered:
        background_list = output.parent / "scene_backgrounds.ffconcat"
        overlay_list = output.parent / "scene_overlays.ffconcat"
        write_concat_list(
            background_list,
            [frame.background_path for frame in valid_frames if frame.background_path],
        )
        write_concat_list(
            overlay_list,
            [frame.overlay_path for frame in valid_frames if frame.overlay_path],
        )
        input_args = [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(background_list),
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(overlay_list),
        ]
        filter_args = [
            "-filter_complex",
            f"[0:v]fps={VIDEO_FPS},{ken_burns_filter(ken_burns_style)}[bg];"
            f"[1:v]fps={VIDEO_FPS},format=rgba[fg];"
            "[bg][fg]overlay=0:0:format=auto,format=yuv420p[outv]",
            "-map",
            "[outv]",
        ]
    args = [
        "-y",
        *input_args,
        *filter_args,
        "-t",
        f"{total_duration:.3f}",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        "-an",
        str(output),
    ]
    _run_ffmpeg(ffmpeg_path, args, timeout=timeout)


def build_mux_args(
    video_path: Path,
    audio_path: Path,
    output: Path,
    *,
    subtitle_path: Path | None,
) -> list[str]:
    """映像+音声合成のffmpeg引数を構築する(subtitle_path指定時のみ字幕を焼き込む)。

    字幕焼き込みはデフォルト無効(`SUBTITLE_BURN_IN_ENABLED`)。YouTubeの自動字幕/
    アップロード字幕に委ねる運用を既定とし、焼き込みは明示オプトインとする。
    """
    vf_args: list[str] = []
    if subtitle_path is not None:
        escaped_subtitle = _escape_subtitles_filter_path(subtitle_path)
        # 注意: SRT焼き込み時のforce_styleの数値はlibassの既定PlayRes(384x288)基準で
        # 解釈され、出力解像度に合わせて拡大される(1080pでは約3.75倍)。
        # 以前はFontSize=34(→約127px)・MarginL/R=150(→約750px)となり文字が
        # はみ出していた。1080p実寸でフォント約49px・左右マージン約120px・下48pxに
        # なるよう384x288基準の値で指定する。行の折り返しはlibassがマージン内で自動処理。
        force_style = (
            "FontName=Noto Sans CJK JP,FontSize=13,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00101010,BorderStyle=3,BackColour=&H90000000,"
            "Outline=1,Shadow=0,Alignment=2,MarginL=24,MarginR=24,MarginV=13"
        )
        vf_args = ["-vf", f"subtitles='{escaped_subtitle}':force_style='{force_style}'"]
    return [
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        *vf_args,
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output),
    ]


def _mux_audio_video(
    ffmpeg_path: str,
    video_path: Path,
    audio_path: Path,
    output: Path,
    *,
    subtitle_path: Path | None,
    timeout: float,
) -> None:
    """映像+音声を合成しH.264+faststartでMP4出力する(字幕焼き込みは任意)。"""
    args = build_mux_args(video_path, audio_path, output, subtitle_path=subtitle_path)
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
    _build_audio_track(
        ffmpeg_path, inputs.section_audio_paths, audio_full, timeout=timeout, mix=inputs.audio_mix
    )
    audio_duration = probe_video(audio_full).duration_seconds
    visual_plan = inputs.visual_variety_plan or pick_visual_variety_plan(
        inputs.video_project_id
    )

    video_main = work_dir / "video_main.mp4"
    if inputs.scene_frames:
        _build_scene_video_track(
            ffmpeg_path,
            inputs.scene_frames,
            video_main,
            timeout=timeout,
            ken_burns_style=visual_plan.ken_burns_style,
        )
    else:
        _build_video_track(
            ffmpeg_path,
            inputs.background_image_path,
            video_main,
            duration_seconds=audio_duration,
            timeout=timeout,
            ken_burns_style=visual_plan.ken_burns_style,
        )

    muxed_main = work_dir / "muxed_main.mp4"
    _mux_audio_video(
        ffmpeg_path,
        video_main,
        audio_full,
        muxed_main,
        subtitle_path=(inputs.subtitle_srt_path if settings.SUBTITLE_BURN_IN_ENABLED else None),
        timeout=timeout,
    )

    if inputs.endcard_enabled:
        endcard_image = work_dir / "endcard.png"
        generate_endcard_image(
            endcard_image,
            title=inputs.title,
            channel_name=inputs.channel_name,
            disclaimer_text=inputs.disclaimer_text,
        )
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
