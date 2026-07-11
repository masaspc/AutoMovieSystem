"""synthesize_audio が VideoProject.target_duration_seconds を常に実測値で
上書きすること(Phase 2)の単体テスト。古い値(過去仕様: 初回のみ設定)が
残り続けないことを検証する。FFmpegは不要(FakeTTSProviderのみ使用)。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.providers.tts.fake import FakeTTSProvider
from app.services.media.pipeline import synthesize_audio


def _make_project_with_script(session: Session) -> VideoProject:
    channel = Channel(name="ch")
    session.add(channel)
    session.flush()

    topic = Topic(channel_id=channel.id, title="t", source_type="manual", source_ref="r-dur")
    session.add(topic)
    session.flush()

    script = Script(
        topic_id=topic.id,
        version=1,
        title="動画タイトル",
        hook="フック",
        body={
            "title_candidates": ["動画タイトル"],
            "target_audience": "初心者",
            "viewer_problem": "課題",
            "promised_outcome": "成果",
            "hook": "フック",
            "sections": [
                {
                    "heading": "導入",
                    "narration": "これはテストのナレーションです。",
                    "visual_instruction": "背景を表示する。",
                    "evidence_ids": [],
                    "dialogue": [],
                },
            ],
            "conclusion": "まとめ",
            "call_to_action": "登録してください",
            "description": "説明",
            "tags": ["tag"],
            "chapters": ["導入"],
        },
        conclusion="まとめ",
        call_to_action="登録してください",
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


def test_synthesize_audio_overwrites_stale_target_duration_seconds(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GENERATED_DIR", str(tmp_path / "generated"))

    project = _make_project_with_script(db_session)
    provider = FakeTTSProvider()

    asyncio.run(synthesize_audio(db_session, video_project_id=project.id, provider=provider))
    db_session.commit()

    correct_value = project.target_duration_seconds
    assert correct_value is not None

    # 過去仕様(初回のみ設定)を模して、古い値を強制的に書き込む。
    project.target_duration_seconds = correct_value + 999
    db_session.commit()

    # 再実行(音声Assetは既存のためTTSは再呼び出しされず冪等スキップされる)しても、
    # target_duration_secondsは古い値を残さず実測値へ上書きされる。
    asyncio.run(synthesize_audio(db_session, video_project_id=project.id, provider=provider))
    db_session.commit()

    assert project.target_duration_seconds == correct_value
