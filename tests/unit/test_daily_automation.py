"""毎日投稿の自動化(夜間自動制作・承認後自動アップロード・実測チャプター)の検証。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.services.media.dialogue import SpeechLine
from app.services.media.pipeline import build_chapter_lines
from app.services.publishing.uploader import _with_auto_chapters
from app.workers.tasks.production import select_auto_produce_topics


@dataclass
class _FakeAsyncResult:
    id: str


def _make_topic(
    db_session: Session, channel: Channel, *, title: str, score: float, source_ref: str
) -> Topic:
    topic = Topic(
        channel_id=channel.id,
        title=title,
        source_type="manual",
        source_ref=source_ref,
        total_score=score,
    )
    db_session.add(topic)
    db_session.flush()
    return topic


def _add_project(db_session: Session, topic: Topic, status: str) -> VideoProject:
    project = VideoProject(topic_id=topic.id, status=status, generation=1)
    db_session.add(project)
    db_session.flush()
    return project


# --- 夜間自動制作の候補選定 ---


def test_select_auto_produce_topics_prefers_high_score_unproduced(db_session: Session) -> None:
    channel = Channel(name="ch")
    db_session.add(channel)
    db_session.flush()

    low = _make_topic(db_session, channel, title="低スコア", score=0.2, source_ref="r-low")
    high = _make_topic(db_session, channel, title="高スコア", score=0.9, source_ref="r-high")
    produced = _make_topic(db_session, channel, title="制作済み", score=1.0, source_ref="r-done")
    _add_project(db_session, produced, "UPLOADED_PRIVATE")
    failed = _make_topic(db_session, channel, title="失敗中", score=0.95, source_ref="r-fail")
    _add_project(db_session, failed, "RENDER_FAILED")
    db_session.commit()

    selected = select_auto_produce_topics(db_session, limit=2)

    # 制作済み(UPLOADED_PRIVATE)と失敗中(人間の判断待ち)は除外され、スコア順に選ばれる。
    assert selected == [high.id, low.id]


def test_select_auto_produce_topics_includes_script_stage_projects(db_session: Session) -> None:
    channel = Channel(name="ch")
    db_session.add(channel)
    db_session.flush()
    topic = _make_topic(db_session, channel, title="台本まで", score=0.5, source_ref="r-s")
    _add_project(db_session, topic, "SCRIPT_REVIEWED")
    db_session.commit()

    assert select_auto_produce_topics(db_session, limit=5) == [topic.id]


# --- 承認後の自動privateアップロード ---


def _approvable_project(db_session: Session) -> VideoProject:
    channel = Channel(name="ch")
    db_session.add(channel)
    db_session.flush()
    topic = Topic(channel_id=channel.id, title="t", source_type="manual", source_ref="r-1")
    db_session.add(topic)
    db_session.flush()
    script = Script(
        topic_id=topic.id,
        version=1,
        title="タイトル",
        body={"sections": []},
        source_manifest={},
        status="reviewed",
    )
    db_session.add(script)
    db_session.flush()
    project = VideoProject(
        topic_id=topic.id, script_id=script.id, status="AUTOMATED_REVIEW_PASSED", generation=1
    )
    db_session.add(project)
    db_session.commit()
    return project


def test_approve_dispatches_private_upload_when_enabled(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _approvable_project(db_session)
    monkeypatch.setattr(
        "app.workers.tasks.publishing.upload_video_task.delay",
        lambda video_project_id: _FakeAsyncResult(id="task-up"),
    )

    get_response = client.get(f"/video-projects/{project.id}/review")
    csrf_token = get_response.cookies["csrf_token"]
    response = client.post(
        f"/video-projects/{project.id}/approve",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    # 承認(人間ゲート)通過後、privateアップロードが自動dispatchされる。
    assert "task_id=task-up" in response.headers["location"]


def test_approve_keeps_manual_flow_when_disabled(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _approvable_project(db_session)
    monkeypatch.setenv("AUTO_UPLOAD_AFTER_APPROVAL", "false")

    get_response = client.get(f"/video-projects/{project.id}/review")
    csrf_token = get_response.cookies["csrf_token"]
    response = client.post(
        f"/video-projects/{project.id}/approve",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "task_id=" not in response.headers["location"]


# --- 実測チャプター ---


def _line(index: int, section_index: int) -> SpeechLine:
    return SpeechLine(
        index=index,
        section_index=section_index,
        speaker="zundamon",
        text="テキスト",
        emotion="neutral",
        visual_instruction="",
    )


def test_build_chapter_lines_uses_measured_durations() -> None:
    sections = [{"heading": "導入"}, {"heading": "本編"}, {"heading": "まとめ"}]
    lines = [_line(0, 0), _line(1, 0), _line(2, 1), _line(3, 2)]
    durations = [30.0, 35.5, 70.0, 20.0]

    chapters = build_chapter_lines(sections, lines, durations)

    # 実測尺の累積からセクション開始時刻を計算する(0:00 / 65.5秒→1:05 / 135.5秒→2:15)。
    assert chapters == ["0:00 導入", "1:05 本編", "2:15 まとめ"]


def test_with_auto_chapters_appends_only_when_youtube_requirements_met() -> None:
    manifest_ok = {"auto_chapters": ["0:00 導入", "1:05 本編", "2:15 まとめ"]}
    result = _with_auto_chapters("説明文", manifest_ok)
    assert "0:00 導入" in result and result.startswith("説明文")

    # 3個未満・先頭0:00でない場合は追記しない(YouTube要件未達)。
    assert _with_auto_chapters("説明文", {"auto_chapters": ["0:00 A", "1:00 B"]}) == "説明文"
    assert (
        _with_auto_chapters("説明文", {"auto_chapters": ["0:30 A", "1:00 B", "2:00 C"]})
        == "説明文"
    )
    # 二重追記しない。
    assert _with_auto_chapters(result, manifest_ok) == result
