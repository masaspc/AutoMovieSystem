"""メディア生成パイプライン(Phase 3): 背景画像準備・TTS音声合成・動画レンダリング。

各処理は `app/services/jobs.py` の `run_idempotent`/`run_idempotent_async` を経由して
冪等に実行し、`VideoProject.status` の変更は必ず `app/services/state_machine.py` の
`transition()` のみで行う。
"""

from __future__ import annotations

import hashlib
import json
import wave
from dataclasses import asdict
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
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
from app.providers.background.factory import get_background_provider
from app.providers.tts.base import TTSProvider
from app.schemas.production_settings import ProductionSettings
from app.services.jobs import JobInProgressError, run_idempotent, run_idempotent_async
from app.services.media import bgm, characters, dialogue, renderer, subtitles, variety, visuals
from app.services.media.probe import inspect_rendered_video
from app.services.state_machine import transition

logger = get_logger(__name__)

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

ASSET_SPEC_VERSION = 3


def build_prepare_assets_idempotency_key(video_project_id: str) -> str:
    return f"prepare_assets:{video_project_id}:v{ASSET_SPEC_VERSION}"


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
    script = _get_script(session, project)
    idempotency_key = build_prepare_assets_idempotency_key(video_project_id)
    visual_plan = variety.pick_visual_variety_plan(video_project_id)

    def _do_prepare() -> VideoProject:
        background_provider = get_background_provider()
        sections = list((script.body or {}).get("sections") or [])
        relative_path = f"videos/{video_project_id}/background.png"
        output_path = resolve_generated_path(relative_path)
        first_section = sections[0] if sections else {"heading": script.title}
        first_section_with_variety = {
            **first_section,
            "_variety_heading_style": visual_plan.heading_style,
            "_variety_accent_hue_shift": visual_plan.accent_hue_shift,
        }
        background_provider.generate(first_section_with_variety, output_path)
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

        for index, section in enumerate(sections):
            section_path = resolve_generated_path(
                f"videos/{video_project_id}/backgrounds/section_{index:02d}.png"
            )
            section_with_variety = {
                **section,
                "_variety_heading_style": visual_plan.heading_style,
                "_variety_accent_hue_shift": visual_plan.accent_hue_shift,
            }
            background_provider.generate(section_with_variety, section_path)
            _upsert_asset(
                session,
                video_project_id=video_project_id,
                asset_type="image",
                role=f"background:section:{index}",
                file_path=section_path,
                checksum=renderer.compute_file_checksum(section_path),
                meta={
                    "role": "section_background",
                    "section_index": index,
                    "visual_type": section.get("visual_type", "dialogue"),
                },
            )

        script.source_manifest = {
            **(script.source_manifest or {}),
            "visual_variety_plan": asdict(visual_plan),
        }

        # シリーズ統一サムネイル自動生成(モジュール分割による循環importを避けるため
        # 遅延import)。候補3案を生成し、role="thumbnail"(選択済み)が未設定なら
        # 候補0を自動でデフォルト選択する(冪等: 既に選択済みなら上書きしない)。
        from app.services.media import thumbnails

        thumbnail_assets = thumbnails.generate_thumbnail_candidates(
            session, video_project_id=video_project_id
        )
        existing_selected = (
            session.query(Asset)
            .filter(
                Asset.video_project_id == video_project_id,
                Asset.role == thumbnails.THUMBNAIL_ROLE_SELECTED,
            )
            .one_or_none()
        )
        if existing_selected is None and thumbnail_assets:
            thumbnails.select_thumbnail(
                session, video_project_id=video_project_id, candidate_index=0
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
# synthesize_audio: セリフごとにTTS合成 + Asset登録。
# ---------------------------------------------------------------------------


def build_section_idempotency_key(
    video_project_id: str,
    section_index: int,
    narration: str,
    speed_scale: float = 1.0,
    emotion: str = "neutral",
) -> str:
    # emotionはVOICEVOXの演技パラメータ(pitch/intonation/speed)に影響するため
    # 冪等キーに含める(感情が変わったセリフは再合成される)。
    text_hash = hashlib.sha256(
        f"{narration}|speed={speed_scale}|emotion={emotion}".encode()
    ).hexdigest()
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
    """Scriptの各セリフをTTSで音声化しAsset登録する(セリフごとに冪等)。"""
    project = _get_video_project(session, video_project_id)
    script = _get_script(session, project)

    settings = get_settings()
    production_settings = ProductionSettings.model_validate(
        project.production_settings or ProductionSettings().model_dump()
    )
    speed_scale = production_settings.speaking_rate
    dialogue_enabled = dialogue.dialogue_script_enabled(settings)
    speech_lines = dialogue.extract_speech_lines(script.body or {}, settings=settings)
    if not speech_lines:
        raise PipelinePreconditionError(f"Script({script.id})に音声化できるセリフがありません")

    assets: list[Asset] = []
    for line in speech_lines:
        index = line.index
        narration = line.text
        idempotency_key = build_section_idempotency_key(
            video_project_id, index, narration, speed_scale, line.emotion
        )
        text_hash = hashlib.sha256(
            f"{narration}|speed={speed_scale}|emotion={line.emotion}".encode()
        ).hexdigest()
        relative_path = f"videos/{video_project_id}/audio/line_{index:02d}_{text_hash[:12]}.wav"
        output_path = resolve_generated_path(relative_path)

        async def _do_synthesize(
            job_run: JobRun,
            *,
            _index: int = index,
            _narration: str = narration,
            _speaker: str = line.speaker,
            _emotion: str = line.emotion,
            _section_index: int = line.section_index,
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
                    voice=(
                        _speaker
                        if dialogue_enabled
                        and settings.TTS_PROVIDER == "voicevox"
                        and voice == DEFAULT_VOICE
                        else voice
                    ),
                    output_path=_output_path,
                    idempotency_key=_idempotency_key,
                    speed_scale=speed_scale,
                    emotion=_emotion,
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
                    "line_index": _index,
                    "section_index": _section_index,
                    "source_section_index": _section_index,
                    "speaker": _speaker,
                    "emotion": _emotion,
                    "duration_seconds": duration_seconds,
                    "sample_rate": sample_rate,
                    "speed_scale": speed_scale,
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
                raise RuntimeError(f"JobRun succeeded but Asset not found for line_index={index}")
            assets.append(match)
        else:
            assert job_result.result is not None
            assets.append(job_result.result)

    # レンダリング・自動レビューが尺の妥当性判定に使うtarget_duration_secondsを、
    # セクション尺が判明したこのタイミングで常に実測値へ上書きする(TTS実測ベースの
    # 期待尺。ユーザー希望尺は production_settings 側の責務)。再実行(台本更新・再合成)
    # で古い値が残り続けないよう、初回のみではなく毎回上書きする。
    total_audio_seconds = sum(float((a.meta or {}).get("duration_seconds", 0.0)) for a in assets)
    project.target_duration_seconds = round(
        total_audio_seconds + renderer.DEFAULT_ENDCARD_DURATION_SECONDS
    )
    session.flush()

    return assets


# ---------------------------------------------------------------------------
# render_video: 字幕生成 -> FFmpegレンダリング -> ffprobe検証。
# ASSETS_READY -> VIDEO_RENDERED。失敗時 RENDER_FAILED。
# ---------------------------------------------------------------------------


def build_render_idempotency_key(video_project_id: str, input_checksum: str) -> str:
    return f"render_video:{video_project_id}:{input_checksum}"


def _fetch_ordered_audio_assets(
    session: Session, video_project_id: str, *, expected_line_count: int
) -> list[Asset]:
    expected_roles = {asset_role_for_audio_section(index) for index in range(expected_line_count)}
    assets = (
        session.query(Asset)
        .filter(
            Asset.video_project_id == video_project_id,
            Asset.asset_type == "audio",
            Asset.role.in_(expected_roles),
        )
        .all()
    )
    return sorted(
        assets,
        key=lambda asset: (asset.meta or {}).get(
            "line_index", (asset.meta or {}).get("section_index", 0)
        ),
    )


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


def _fetch_section_backgrounds(
    session: Session, video_project_id: str, fallback: Asset
) -> dict[int, Asset]:
    assets = (
        session.query(Asset)
        .filter(
            Asset.video_project_id == video_project_id,
            Asset.role.like("background:section:%"),
        )
        .all()
    )
    result = {int((asset.meta or {}).get("section_index", 0)): asset for asset in assets}
    if not result:
        result[0] = fallback
    return result


# レンダリング演出の実装(キャラ配置・字幕・音響・トランジション等)を変えたら必ず上げる。
# 入力チェックサムに含まれるため、古い実装で生成済みの動画キャッシュを再利用しなくなる。
# v2: キャラ両端配置+右側反転+上下移動廃止、字幕焼き込みのデフォルトOFF化、
#     scene concatの尺クランプ修正。
# v3: BGMダッキングミックス+セクション切替SE(Phase A: 音響)。
# v4: キーワードテロップ、グラフ、コード色分け、背景プロバイダー抽象化。
# v5: 教材背景のピクセル幅フィットとコード抽出。
# v6: 決定論的Ken Burns・場面転換・見出し・アクセント配色バリエーション。
RENDER_SPEC_VERSION = 6


def _compute_render_input_checksum(
    script: Script,
    audio_assets: list[Asset],
    background_assets: list[Asset],
    *,
    aspect_ratio: str,
    endcard_enabled: bool,
    endcard_duration_seconds: float,
    character_fingerprint: str,
    subtitle_burn_in: bool,
    audio_fingerprint: str = "audio-mix-disabled",
) -> str:
    """script本文+各Assetのchecksum+レンダリング設定からレンダリング入力のハッシュを計算する。"""
    hasher = hashlib.sha256()
    hasher.update(json.dumps(script.body or {}, sort_keys=True, ensure_ascii=True).encode("utf-8"))
    for asset in audio_assets:
        hasher.update(asset.checksum.encode("utf-8"))
    for background_asset in background_assets:
        hasher.update(background_asset.checksum.encode("utf-8"))
    hasher.update(character_fingerprint.encode("utf-8"))
    hasher.update(audio_fingerprint.encode("utf-8"))
    hasher.update(
        f"|spec={RENDER_SPEC_VERSION}|aspect_ratio={aspect_ratio}|endcard={endcard_enabled}|"
        f"endcard_duration={endcard_duration_seconds}|subtitle_burn_in={subtitle_burn_in}".encode()
    )
    return hasher.hexdigest()


def _resolve_channel_name(session: Session, project: VideoProject) -> str:
    """エンドカード等に表示するチャンネル名をDBから解決する。

    システム名(AutoMovieSystem)を視聴者向けの動画に出さないため、
    Topic経由で実チャンネル名を引く。見つからない場合は空文字(表示なし)。
    """
    from app.models.channel import Channel
    from app.models.topic import Topic

    topic = session.get(Topic, project.topic_id)
    if topic is None:
        return ""
    channel = session.get(Channel, topic.channel_id)
    return channel.name if channel is not None else ""


def build_chapter_lines(
    sections: list[dict], speech_lines: list, durations: list[float]
) -> list[str]:
    """実測音声尺からYouTubeチャプター行("M:SS 見出し")を組み立てる。

    LLMが推測したchapters(body.chapters)は実際の尺とずれるため、レンダリング時に
    実測値で作り直す。YouTubeのチャプター要件(先頭0:00・3個以上)は
    アップロード側(_with_auto_chapters)が判定する。
    """
    chapter_lines: list[str] = []
    elapsed = 0.0
    seen: set[int] = set()
    for line, duration in zip(speech_lines, durations, strict=True):
        if line.section_index not in seen:
            seen.add(line.section_index)
            heading = (
                str(sections[line.section_index].get("heading") or "")
                if line.section_index < len(sections)
                else ""
            )
            minutes, seconds = divmod(int(elapsed), 60)
            chapter_lines.append(f"{minutes}:{seconds:02d} {heading}".rstrip())
        elapsed += duration
    return chapter_lines


def render_video(
    session: Session,
    *,
    video_project_id: str,
    channel_name: str | None = None,
    title: str | None = None,
) -> VideoProject:
    """字幕生成+FFmpegレンダリング+ffprobe検証を行う(冪等)。

    `ASSETS_READY` -> `VIDEO_RENDERED`。FFmpeg失敗またはprobe検査でblocking findingsが
    ある場合は `RENDER_FAILED` へ遷移し `PipelineRenderError` を送出する。

    channel_name未指定時はDBの実チャンネル名を使う(視聴者向けの動画へ
    システム名を出さない)。
    """
    project = _get_video_project(session, video_project_id)
    script = _get_script(session, project)
    if channel_name is None:
        channel_name = _resolve_channel_name(session, project)

    settings = get_settings()
    dialogue_enabled = dialogue.dialogue_script_enabled(settings)
    speech_lines = dialogue.extract_speech_lines(script.body or {}, settings=settings)
    if not speech_lines:
        raise PipelinePreconditionError(f"Script({script.id})に音声化できるセリフがありません")

    audio_assets = _fetch_ordered_audio_assets(
        session, video_project_id, expected_line_count=len(speech_lines)
    )
    if len(audio_assets) != len(speech_lines):
        raise PipelinePreconditionError(
            f"音声Asset数({len(audio_assets)})がセリフ数({len(speech_lines)})と一致しません"
            "(synthesize_audio未実行または不完全)"
        )
    background_asset = _fetch_background_asset(session, video_project_id)
    section_background_assets = _fetch_section_backgrounds(
        session, video_project_id, background_asset
    )

    endcard_enabled = True
    endcard_duration_seconds = renderer.DEFAULT_ENDCARD_DURATION_SECONDS
    character_render_enabled = dialogue_enabled and settings.CHARACTER_RENDER_ENABLED
    character_fingerprint = (
        characters.character_assets_fingerprint(settings, speech_lines)
        if character_render_enabled
        else "characters-disabled"
    )

    # --- 音響(Phase A): BGM選曲とSE配置。素材が無ければ無音のまま完走する ---
    production_settings = ProductionSettings.model_validate(
        project.production_settings or ProductionSettings().model_dump()
    )
    visual_plan = variety.pick_visual_variety_plan(video_project_id)
    section_durations = [float((a.meta or {}).get("duration_seconds", 0.0)) for a in audio_assets]
    bgm_track = bgm.select_bgm(
        video_project_id=video_project_id, mood=production_settings.bgm_mood, settings=settings
    )
    sound_effects: list[bgm.SoundEffect] = []
    if production_settings.se_enabled:
        section_start_offsets: list[float] = []
        elapsed = 0.0
        previous_section = None
        for line, duration in zip(speech_lines, section_durations, strict=True):
            if previous_section is not None and line.section_index != previous_section:
                section_start_offsets.append(elapsed)
            previous_section = line.section_index
            elapsed += duration
        sound_effects = bgm.transition_effects(section_start_offsets, settings)
    audio_fingerprint = "|".join(
        [
            f"bgm={bgm_track.checksum if bgm_track else 'none'}",
            f"bgm_volume={production_settings.bgm_volume_db:.1f}",
            f"se={bgm.effects_fingerprint(sound_effects)}",
            f"se_volume={production_settings.se_volume_db:.1f}",
        ]
    )

    input_checksum = _compute_render_input_checksum(
        script,
        audio_assets,
        [section_background_assets[index] for index in sorted(section_background_assets)],
        aspect_ratio=project.aspect_ratio,
        endcard_enabled=endcard_enabled,
        endcard_duration_seconds=endcard_duration_seconds,
        character_fingerprint=character_fingerprint,
        subtitle_burn_in=settings.SUBTITLE_BURN_IN_ENABLED,
        audio_fingerprint=audio_fingerprint,
    )
    idempotency_key = build_render_idempotency_key(video_project_id, input_checksum)

    def _do_render() -> VideoProject:
        subtitle_sections = [{"narration": line.text} for line in speech_lines]
        cues = subtitles.build_cues(subtitle_sections, section_durations=section_durations)
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

        background_paths = {
            index: Path(asset.file_path) for index, asset in section_background_assets.items()
        }
        scene_frames = (
            characters.build_scene_frames(
                backgrounds=background_paths,
                lines=speech_lines,
                durations=section_durations,
                sections=list((script.body or {}).get("sections") or []),
                settings=settings,
                output_dir=srt_path.parent / f"scenes_{input_checksum[:16]}",
                transition_style=visual_plan.transition_style,
            )
            if character_render_enabled
            else visuals.build_background_frames(
                background_paths,
                [line.section_index for line in speech_lines],
                section_durations,
            )
        )

        # 実測尺ベースの正確なチャプターを保存する(アップロード時に概要欄へ自動追記)。
        auto_chapters = build_chapter_lines(
            list((script.body or {}).get("sections") or []), speech_lines, section_durations
        )
        script.source_manifest = {**(script.source_manifest or {}), "auto_chapters": auto_chapters}

        manifest_path = visuals.write_scene_manifest(
            list((script.body or {}).get("sections") or []),
            [line.section_index for line in speech_lines],
            section_durations,
            resolve_generated_path(
                f"videos/{video_project_id}/scene_manifest_{input_checksum[:16]}.json"
            ),
        )

        audio_mix = renderer.AudioMixSpec(
            bgm_path=bgm_track.path if bgm_track else None,
            bgm_volume_db=production_settings.bgm_volume_db,
            effects=[
                (effect.path, effect.offset_seconds, production_settings.se_volume_db)
                for effect in sound_effects
            ],
        )
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
            scene_frames=scene_frames,
            audio_mix=audio_mix,
            visual_variety_plan=visual_plan,
        )

        try:
            result = renderer.render_video(render_inputs)
        except (characters.CharacterAssetError, renderer.RenderError, NotImplementedError) as exc:
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

        # 検査済みの動画で実際に使ったBGMだけを記録する。BGMなしで再生成した場合は、
        # 以前のクレジットが概要欄へ残らないよう古いAssetを削除する。
        previous_bgm_asset = (
            session.query(Asset)
            .filter(Asset.video_project_id == video_project_id, Asset.role == "bgm")
            .one_or_none()
        )
        if bgm_track is None:
            if previous_bgm_asset is not None:
                session.delete(previous_bgm_asset)
        else:
            _upsert_asset(
                session,
                video_project_id=video_project_id,
                asset_type="audio",
                role="bgm",
                file_path=bgm_track.path,
                checksum=bgm_track.checksum,
                meta={
                    "mood": bgm_track.mood,
                    "credit": bgm_track.credit,
                    "volume_db": production_settings.bgm_volume_db,
                },
            )

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
        _upsert_asset(
            session,
            video_project_id=video_project_id,
            asset_type="other",
            role="scene_manifest",
            file_path=manifest_path,
            checksum=renderer.compute_file_checksum(manifest_path),
            meta={"kind": "scene_timeline"},
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


def restart_render(session: Session, *, video_project_id: str) -> VideoProject:
    """再試行: `RENDER_FAILED` -> `ASSETS_READY`(復旧エッジ)。"""
    project = _get_video_project(session, video_project_id)
    transition(project, "ASSETS_READY")
    session.flush()
    return project
