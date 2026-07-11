"""実FFmpeg/ffprobeを使ったメディアレンダリングパイプラインの検証(@pytest.mark.media)。

このテストはffmpeg/ffprobeの実バイナリを必要とする。設定(`FFMPEG_PATH`/`FFPROBE_PATH`
未指定時の解決ロジック)経由でバイナリが見つからない場合はskipする。
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.channel import Channel
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.providers.tts.fake import FakeTTSProvider
from app.services.media.pipeline import prepare_assets, render_video, synthesize_audio
from app.services.media.probe import probe_video

pytestmark = pytest.mark.media


class RecordingFakeTTSProvider(FakeTTSProvider):
    def __init__(self) -> None:
        self.voices: list[str] = []

    async def synthesize(self, **kwargs):  # type: ignore[no-untyped-def]
        self.voices.append(kwargs["voice"])
        return await super().synthesize(**kwargs)


def _ffmpeg_available() -> bool:
    settings = get_settings()
    return bool(shutil.which(settings.resolved_ffmpeg_path) or settings.resolved_ffmpeg_path)


@pytest.fixture
def media_generated_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """メディア生成物を一時ディレクトリへ隔離する(リポジトリのgenerated/を汚さない)。"""
    monkeypatch.setenv("GENERATED_DIR", str(tmp_path / "generated"))
    return tmp_path / "generated"


def _make_video_project_with_script(session: Session) -> VideoProject:
    channel = Channel(name="test-channel")
    session.add(channel)
    session.flush()

    topic = Topic(
        channel_id=channel.id,
        title="FFmpeg統合テスト企画",
        source_type="manual",
        source_ref="media-test-1",
    )
    session.add(topic)
    session.flush()

    script = Script(
        topic_id=topic.id,
        version=1,
        title="FFmpeg統合テスト動画",
        hook="このテストはFFmpegの実バイナリを検証します",
        body={
            "title_candidates": ["FFmpeg統合テスト動画"],
            "target_audience": "開発者",
            "viewer_problem": "レンダリングパイプラインの動作確認",
            "promised_outcome": "実ffmpegでの生成・検証が完了する",
            "hook": "このテストはFFmpegの実バイナリを検証します",
            "sections": [
                {
                    "heading": "導入",
                    "narration": "これはテストの導入部分です。",
                    "visual_instruction": "背景画像を表示する。",
                    "evidence_ids": [],
                },
                {
                    "heading": "本編",
                    "narration": "本編の内容を短く説明します。",
                    "visual_instruction": "背景画像を継続表示する。",
                    "evidence_ids": [],
                },
            ],
            "conclusion": "以上でテストは終了です。",
            "call_to_action": "結果を確認してください。",
            "description": "統合テスト用の説明文です。",
            "tags": ["test"],
            "chapters": ["導入", "本編"],
        },
        conclusion="以上でテストは終了です。",
        call_to_action="結果を確認してください。",
        source_manifest={"evidence_ids": []},
        status="reviewed",
    )
    session.add(script)
    session.flush()

    project = VideoProject(
        topic_id=topic.id,
        script_id=script.id,
        status="SCRIPT_REVIEWED",
        generation=1,
        aspect_ratio="16:9",
    )
    session.add(project)
    session.flush()
    return project


def test_full_media_pipeline_produces_valid_mp4(db_session: Session, media_generated_dir) -> None:
    if not _ffmpeg_available():
        pytest.skip("ffmpeg/ffprobeが見つかりません")

    project = _make_video_project_with_script(db_session)

    # 1. 背景画像準備: SCRIPT_REVIEWED -> ASSETS_READY
    project = prepare_assets(db_session, video_project_id=project.id)
    db_session.commit()
    assert project.status == "ASSETS_READY"

    # 2. TTS音声合成(Fake)
    provider = FakeTTSProvider()
    assets = asyncio.run(
        synthesize_audio(db_session, video_project_id=project.id, provider=provider)
    )
    db_session.commit()
    assert len(assets) == 2

    # 3. FFmpegレンダリング + ffprobe検証: ASSETS_READY -> VIDEO_RENDERED
    project = render_video(db_session, video_project_id=project.id, channel_name="テストチャンネル")
    db_session.commit()

    assert project.status == "VIDEO_RENDERED"
    assert project.output_path is not None
    assert project.checksum is not None

    output_file = Path(project.output_path)
    assert output_file.exists()
    assert output_file.stat().st_size > 0

    probe_result = probe_video(output_file)
    print(
        "ffprobe summary:",
        {
            "duration_seconds": probe_result.duration_seconds,
            "width": probe_result.width,
            "height": probe_result.height,
            "fps": probe_result.fps,
            "video_codec": probe_result.video_codec,
            "audio_codec": probe_result.audio_codec,
            "has_audio": probe_result.has_audio,
            "size_bytes": probe_result.size_bytes,
        },
    )

    assert probe_result.width == 1920
    assert probe_result.height == 1080
    assert probe_result.video_codec == "h264"
    assert probe_result.has_audio is True
    assert probe_result.audio_codec == "aac"
    # 音声(短い2セクション、最低1秒ずつ)+エンドカード3秒 = 5〜10秒程度を期待。
    assert 4.0 <= probe_result.duration_seconds <= 12.0


def test_render_video_is_idempotent_and_skips_second_render(
    db_session: Session, media_generated_dir
) -> None:
    if not _ffmpeg_available():
        pytest.skip("ffmpeg/ffprobeが見つかりません")

    project = _make_video_project_with_script(db_session)
    prepare_assets(db_session, video_project_id=project.id)
    db_session.commit()

    provider = FakeTTSProvider()
    asyncio.run(synthesize_audio(db_session, video_project_id=project.id, provider=provider))
    db_session.commit()

    project = render_video(db_session, video_project_id=project.id)
    db_session.commit()
    first_checksum = project.checksum
    first_output_path = project.output_path

    # 同一入力で再実行 -> JobRun経由で冪等スキップされ、同じ成果物を指す。
    project_again = render_video(db_session, video_project_id=project.id)
    db_session.commit()

    assert project_again.checksum == first_checksum
    assert project_again.output_path == first_output_path


def test_dialogue_is_ignored_for_existing_generic_tts_flow(
    db_session: Session, media_generated_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DIALOGUE_SCRIPT_ENABLED", "false")
    monkeypatch.setenv("TTS_PROVIDER", "generic_command")
    get_settings.cache_clear()

    project = _make_video_project_with_script(db_session)
    script = db_session.get(Script, project.script_id)
    assert script is not None
    body = dict(script.body)
    sections = [dict(section) for section in body["sections"]]
    sections[0]["dialogue"] = [{"speaker": "metan", "text": "使ってはいけないセリフ"}]
    body["sections"] = sections
    script.body = body
    db_session.flush()

    provider = RecordingFakeTTSProvider()
    audio_assets = asyncio.run(
        synthesize_audio(db_session, video_project_id=project.id, provider=provider)
    )

    assert len(audio_assets) == 2
    assert provider.voices == ["default", "default"]
    assert all((asset.meta or {}).get("speaker") == "zundamon" for asset in audio_assets)


def test_dialogue_render_with_character_frames_produces_valid_mp4(
    db_session: Session, media_generated_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not _ffmpeg_available():
        pytest.skip("ffmpeg/ffprobeが見つかりません")

    assets_dir = tmp_path / "characters"
    for character, color in {
        "zundamon": (88, 180, 118, 255),
        "metan": (180, 100, 178, 255),
        "tsumugi": (210, 156, 78, 255),
    }.items():
        directory = assets_dir / character
        directory.mkdir(parents=True)
        Image.new("RGBA", (360, 720), color).save(directory / "normal.png")
        Image.new("RGBA", (360, 720), (*color[:3], 220)).save(directory / "talk.png")

    monkeypatch.setenv("CHARACTER_RENDER_ENABLED", "true")
    monkeypatch.setenv("CHARACTER_ASSETS_DIR", str(assets_dir))
    monkeypatch.setenv("DIALOGUE_SCRIPT_ENABLED", "true")
    monkeypatch.setenv("TTS_PROVIDER", "voicevox")
    get_settings.cache_clear()

    project = _make_video_project_with_script(db_session)
    script = db_session.get(Script, project.script_id)
    assert script is not None
    body = dict(script.body)
    sections = [dict(section) for section in body["sections"]]
    sections[0]["dialogue"] = [
        {"speaker": "zundamon", "text": "こんにちはなのだ。", "emotion": "happy"},
        {"speaker": "metan", "text": "今日は解説します。", "emotion": "serious"},
    ]
    sections[1]["dialogue"] = [
        {"speaker": "tsumugi", "text": "補足をお届けします。", "emotion": "neutral"},
    ]
    body["sections"] = sections
    script.body = body
    db_session.flush()

    prepare_assets(db_session, video_project_id=project.id)
    db_session.commit()
    provider = RecordingFakeTTSProvider()
    audio_assets = asyncio.run(
        synthesize_audio(db_session, video_project_id=project.id, provider=provider)
    )
    db_session.commit()
    assert len(audio_assets) == 3
    assert provider.voices == ["zundamon", "metan", "tsumugi"]

    rendered = render_video(db_session, video_project_id=project.id)
    db_session.commit()

    assert rendered.status == "VIDEO_RENDERED"
    assert rendered.output_path is not None
    probe_result = probe_video(Path(rendered.output_path))
    assert probe_result.width == 1920
    assert probe_result.height == 1080
    assert probe_result.has_audio is True
