"""実FFmpeg/ffprobeを使ったメディアレンダリングパイプラインの検証(@pytest.mark.media)。

このテストはffmpeg/ffprobeの実バイナリを必要とする。設定(`FFMPEG_PATH`/`FFPROBE_PATH`
未指定時の解決ロジック)経由でバイナリが見つからない場合はskipする。
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
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
