"""メディア生成パイプライン(Phase 3): 背景画像準備・TTS音声合成・動画レンダリング。

各処理は `app/services/jobs.py` の `run_idempotent`/`run_idempotent_async` を経由して
冪等に実行し、`VideoProject.status` の変更は必ず `app/services/state_machine.py` の
`transition()` のみで行う。
"""

from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.paths import resolve_generated_path
from app.models.asset import (
    ASSET_ROLE_BACKGROUND,
    ASSET_ROLE_SUBTITLE_SRT,
    ASSET_ROLE_SUBTITLE_VTT,
    Asset,
    asset_role_for_audio_section,
)
from app.models.job_run import JobRun
from app.models.script import Script
from app.models.video_project import VideoProject
from app.providers.tts.base import TTSProvider
from app.services.jobs import JobInProgressError, run_idempotent, run_idempotent_async
from app.services.media import renderer, subtitles
from app.services.media.probe import inspect_rendered_video
from app.services.state_machine import transition

logger = get_logger(__name__)

DEFAULT_CHANNEL_NAME = "AutoMovieSystem"
DEFAULT_VOICE = "default"


class VideoProjectNotFoundError(ValueError):
    """指定されたVideoProjectが存在しない場合。"""


class ScriptNotFoundError(ValueError):
    """VideoProjectに紐づくScriptが存在しない、または未設定の場合。"""


class PipelinePreconditionError(ValueError):
    """レンダリング前提条件(音声Asset不足等)を満たさない場合。"""


class PipelineRenderError(RuntimeError):
    """レンダリング失敗(FFmpeg失敗 or probe検査でblocking findings)。"""


def _get_video_project(session: Session, video_project_id: str) -> VideoProject:
    project = session.get(VideoProject, video_project_id)
    if project is None:
        raise VideoProjectNotFoundError(f"VideoProject not found: {video_project_id}")
    return project


def _get_script(session: Session, project: VideoProject) -> Script:
    if project.script_id is None:
        raise ScriptNotFoundError(f"VideoProject({project.id})にScriptが紐づいていません")
    script = session.get(Script, project.script_id)
    if script is None:
        raise ScriptNotFoundError(f"Script not found: {project.script_id}")
    return script


# ---------------------------------------------------------------------------
# prepare_assets: 背景画像生成 + Asset登録。SCRIPT_REVIEWED -> ASSETS_READY。
# ---------------------------------------------------------------------------


def build_prepare_assets_idempotency_key(video_project_id: str) -> str:
    return f"prepare_assets:{video_project_id}"


def _upsert_asset(
    session: Session,
    *,
    video_project_id: str,
    asset_type: str,
    role: str,
    file_path: Path,
    checksum: str,
    meta: dict,
) -> Asset:
    """既存Assetがあれば更新、なければ新規作成する。

    同一性は `UNIQUE(video_project_id, role)` 制約で判定する(メタデータ依存の同一性
    判定は廃止)。並行実行等でINTEGRITY違反になった場合は既存行を取得して更新する。
    """
    existing = (
        session.query(Asset)
        .filter(Asset.video_project_id == video_project_id, Asset.role == role)
        .one_or_none()
    )

    if existing is not None:
        existing.asset_type = asset_type
        existing.file_path = str(file_path)
        existing.checksum = checksum
        existing.meta = meta
        return existing

    asset = Asset(
        video_project_id=video_project_id,
        asset_type=asset_type,
        role=role,
        file_path=str(file_path),
        source="generated",
        checksum=checksum,
        meta=meta,
    )
    try:
        with session.begin_nested():
            session.add(asset)
            session.flush()
    except IntegrityError:
        # 並行実行で他が先に同じroleでINSERTした -> 自分は追加せず既存行を取得して更新する。
        session.expunge(asset)
        winner = (
            session.query(Asset)
            .filter(Asset.video_project_id == video_project_id, Asset.role == role)
            .one_or_none()
        )
        if winner is None:  # pragma: no cover - 理論上到達しない防御的分岐
            raise
        winner.asset_type = asset_type
        winner.file_path = str(file_path)
        winner.checksum = checksum
        winner.meta = meta
        return winner
    return asset


def prepare_assets(session: Session, *, video_project_id: str) -> VideoProject:
    """背景画像を生成しAsset登録する(冪等)。`SCRIPT_REVIEWED` -> `ASSETS_READY`。"""
    project = _get_video_project(session, video_project_id)
    idempotency_key = build_prepare_assets_idempotency_key(video_project_id)

    def _do_prepare() -> VideoProject:
        relative_path = f"videos/{video_project_id}/background.png"
        output_path = resolve_generated_path(relative_path)
        if not output_path.exists():
            renderer.generate_background_image(output_path)
        checksum = renderer.compute_file_checksum(output_path)

        _upsert_asset(
            session,
            video_project_id=video_project_id,
            asset_type="image",
            role=ASSET_ROLE_BACKGROUND,
            file_path=output_path,
            checksum=checksum,
            meta={"role": "background"},
        )

        transition(project, "ASSETS_READY")
        session.flush()
        return project

    job_result = run_idempotent(
        session,
        job_type="prepare_assets",
        entity_type="video_project",
        entity_id=video_project_id,
        idempotency_key=idempotency_key,
        fn=_do_prepare,
    )
    if job_result.status == "in_progress":
        raise JobInProgressError(f"prepare_assets already in progress: {idempotency_key}")
    if job_result.status == "skipped":
        return project
    assert job_result.result is not None
    return job_result.result


# ---------------------------------------------------------------------------
# synthesize_audio: セクションごとにTTS合成 + Asset登録。
# ---------------------------------------------------------------------------


def build_section_idempotency_key(video_project_id: str, section_index: int, narration: str) -> str:
    text_hash = hashlib.sha256(narration.encode("utf-8")).hexdigest()
    return f"synthesize_audio:{video_project_id}:{section_index}:{text_hash}"


def _read_wav_duration_and_sample_rate(path: Path) -> tuple[float, int]:
    with wave.open(str(path), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        n_frames = wav_file.getnframes()
        duration = (n_frames / sample_rate) if sample_rate else 0.0
    return duration, sample_rate


async def synthesize_audio(
    session: Session,
    *,
    video_project_id: str,
    provider: TTSProvider,
    voice: str = DEFAULT_VOICE,
) -> list[Asset]:
    """Scriptの各sectionをTTSで音声化しAsset登録する(セクションごとに冪等)。"""
    project = _get_video_project(session, video_project_id)
    script = _get_script(session, project)

    sections = (script.body or {}).get("sections") or []
    if not sections:
        raise PipelinePreconditionError(f"Script({script.id})にsectionsがありません")

    assets: list[Asset] = []
    for index, section in enumerate(sections):
        narration = section.get("narration", "")
        idempotency_key = build_section_idempotency_key(video_project_id, index, narration)
        text_hash = hashlib.sha256(narration.encode("utf-8")).hexdigest()
        relative_path = f"videos/{video_project_id}/audio/section_{index:02d}_{text_hash[:12]}.wav"
        output_path = resolve_generated_path(relative_path)

        async def _do_synthesize(
            job_run: JobRun,
            *,
            _index: int = index,
            _narration: str = narration,
            _output_path: Path = output_path,
            _idempotency_key: str = idempotency_key,
        ) -> Asset:
            if _output_path.exists():
                # 成果物(音声)は生成前に既存ファイルを照合し、あれば再生成しない。
                checksum = renderer.compute_file_checksum(_output_path)
                duration_seconds, sample_rate = _read_wav_duration_and_sample_rate(_output_path)
            else:
                result = await provider.synthesize(
                    text=_narration,
                    voice=voice,
                    output_path=_output_path,
                    idempotency_key=_idempotency_key,
                )
                checksum = result.checksum
                duration_seconds = result.duration_seconds
                sample_rate = result.sample_rate

            asset = _upsert_asset(
                session,
                video_project_id=video_project_id,
                asset_type="audio",
                role=asset_role_for_audio_section(_index),
                file_path=_output_path,
                checksum=checksum,
                meta={
                    "section_index": _index,
                    "duration_seconds": duration_seconds,
                    "sample_rate": sample_rate,
                },
            )
            session.flush()
            return asset

        job_result = await run_idempotent_async(
            session,
            job_type="synthesize_audio",
            entity_type="video_project",
            entity_id=video_project_id,
            idempotency_key=idempotency_key,
            fn=_do_synthesize,
        )
        if job_result.status == "in_progress":
            raise JobInProgressError(f"synthesize_audio already in progress: {idempotency_key}")
        if job_result.status == "skipped":
            match = (
                session.query(Asset)
                .filter(
                    Asset.video_project_id == video_project_id,
                    Asset.role == asset_role_for_audio_section(index),
                )
                .one_or_none()
            )
            if match is None:  # pragma: no cover - 理論上到達しない防御的分岐
                raise RuntimeError(
                    f"JobRun succeeded but Asset not found for section_index={index}"
                )
            assets.append(match)
        else:
            assert job_result.result is not None
            assets.append(job_result.result)

    return assets


# ---------------------------------------------------------------------------
# render_video: 字幕生成 -> FFmpegレンダリング -> ffprobe検証。
# ASSETS_READY -> VIDEO_RENDERED。失敗時 RENDER_FAILED。
# ---------------------------------------------------------------------------


def build_render_idempotency_key(video_project_id: str, input_checksum: str) -> str:
    return f"render_video:{video_project_id}:{input_checksum}"


def _fetch_ordered_audio_assets(session: Session, video_project_id: str) -> list[Asset]:
    assets = (
        session.query(Asset)
        .filter(Asset.video_project_id == video_project_id, Asset.asset_type == "audio")
        .all()
    )
    return sorted(assets, key=lambda a: (a.meta or {}).get("section_index", 0))


def _fetch_background_asset(session: Session, video_project_id: str) -> Asset:
    asset = (
        session.query(Asset)
        .filter(
            Asset.video_project_id == video_project_id,
            Asset.role == ASSET_ROLE_BACKGROUND,
        )
        .one_or_none()
    )
    if asset is None:
        raise PipelinePreconditionError(
            f"VideoProject({video_project_id})に背景画像Assetがありません(prepare_assets未実行)"
        )
    return asset


def _compute_render_input_checksum(
    script: Script,
    audio_assets: list[Asset],
    background_asset: Asset,
    *,
    aspect_ratio: str,
    endcard_enabled: bool,
    endcard_duration_seconds: float,
) -> str:
    """script本文+各Assetのchecksum+レンダリング設定からレンダリング入力のハッシュを計算する。"""
    hasher = hashlib.sha256()
    hasher.update(json.dumps(script.body or {}, sort_keys=True, ensure_ascii=True).encode("utf-8"))
    for asset in audio_assets:
        hasher.update(asset.checksum.encode("utf-8"))
    hasher.update(background_asset.checksum.encode("utf-8"))
    hasher.update(
        f"|aspect_ratio={aspect_ratio}|endcard={endcard_enabled}|"
        f"endcard_duration={endcard_duration_seconds}".encode()
    )
    return hasher.hexdigest()


def render_video(
    session: Session,
    *,
    video_project_id: str,
    channel_name: str = DEFAULT_CHANNEL_NAME,
    title: str | None = None,
) -> VideoProject:
    """字幕生成+FFmpegレンダリング+ffprobe検証を行う(冪等)。

    `ASSETS_READY` -> `VIDEO_RENDERED`。FFmpeg失敗またはprobe検査でblocking findingsが
    ある場合は `RENDER_FAILED` へ遷移し `PipelineRenderError` を送出する。
    """
    project = _get_video_project(session, video_project_id)
    script = _get_script(session, project)

    sections = (script.body or {}).get("sections") or []
    if not sections:
        raise PipelinePreconditionError(f"Script({script.id})にsectionsがありません")

    audio_assets = _fetch_ordered_audio_assets(session, video_project_id)
    if len(audio_assets) != len(sections):
        raise PipelinePreconditionError(
            f"音声Asset数({len(audio_assets)})がsection数({len(sections)})と一致しません"
            "(synthesize_audio未実行または不完全)"
        )
    background_asset = _fetch_background_asset(session, video_project_id)

    endcard_enabled = True
    endcard_duration_seconds = renderer.DEFAULT_ENDCARD_DURATION_SECONDS

    input_checksum = _compute_render_input_checksum(
        script,
        audio_assets,
        background_asset,
        aspect_ratio=project.aspect_ratio,
        endcard_enabled=endcard_enabled,
        endcard_duration_seconds=endcard_duration_seconds,
    )
    idempotency_key = build_render_idempotency_key(video_project_id, input_checksum)

    def _do_render() -> VideoProject:
        section_durations = [
            float((a.meta or {}).get("duration_seconds", 0.0)) for a in audio_assets
        ]
        cues = subtitles.build_cues(sections, section_durations=section_durations)
        srt_text = subtitles.render_srt(cues)
        vtt_text = subtitles.render_vtt(cues)

        srt_path = resolve_generated_path(
            f"videos/{video_project_id}/subtitles_{input_checksum[:16]}.srt"
        )
        vtt_path = resolve_generated_path(
            f"videos/{video_project_id}/subtitles_{input_checksum[:16]}.vtt"
        )
        srt_path.parent.mkdir(parents=True, exist_ok=True)
        srt_path.write_text(srt_text, encoding="utf-8")
        vtt_path.write_text(vtt_text, encoding="utf-8")

        render_inputs = renderer.RenderInputs(
            video_project_id=video_project_id,
            aspect_ratio=project.aspect_ratio,
            section_audio_paths=[Path(a.file_path) for a in audio_assets],
            background_image_path=Path(background_asset.file_path),
            subtitle_srt_path=srt_path,
            title=title or script.title,
            channel_name=channel_name,
            input_checksum=input_checksum,
            endcard_enabled=endcard_enabled,
            endcard_duration_seconds=endcard_duration_seconds,
        )

        try:
            result = renderer.render_video(render_inputs)
        except (renderer.RenderError, NotImplementedError) as exc:
            transition(project, "RENDER_FAILED")
            session.flush()
            raise PipelineRenderError(f"レンダリングに失敗しました: {exc}") from exc

        total_expected_duration = sum(section_durations) + (
            endcard_duration_seconds if endcard_enabled else 0.0
        )
        findings = inspect_rendered_video(
            result.output_path,
            expected_min_duration=max(0.0, total_expected_duration - 2.0),
            expected_max_duration=total_expected_duration + 2.0,
            min_width=renderer.VIDEO_WIDTH_16_9,
            min_height=renderer.VIDEO_HEIGHT_16_9,
        )
        blocking = [f for f in findings if f.severity == "blocking"]
        if blocking:
            transition(project, "RENDER_FAILED")
            session.flush()
            raise PipelineRenderError(
                f"レンダリング結果の検査に失敗しました: {[f.message for f in blocking]}"
            )

        project.output_path = str(result.output_path)
        project.checksum = result.checksum
        transition(project, "VIDEO_RENDERED")

        _upsert_asset(
            session,
            video_project_id=video_project_id,
            asset_type="subtitle",
            role=ASSET_ROLE_SUBTITLE_SRT,
            file_path=srt_path,
            checksum=renderer.compute_file_checksum(srt_path),
            meta={"kind": "srt"},
        )
        _upsert_asset(
            session,
            video_project_id=video_project_id,
            asset_type="subtitle",
            role=ASSET_ROLE_SUBTITLE_VTT,
            file_path=vtt_path,
            checksum=renderer.compute_file_checksum(vtt_path),
            meta={"kind": "vtt"},
        )

        session.flush()
        return project

    job_result = run_idempotent(
        session,
        job_type="render_video",
        entity_type="video_project",
        entity_id=video_project_id,
        idempotency_key=idempotency_key,
        fn=_do_render,
    )
    if job_result.status == "in_progress":
        raise JobInProgressError(f"render_video already in progress: {idempotency_key}")
    if job_result.status == "skipped":
        return project
    assert job_result.result is not None
    return job_result.result
