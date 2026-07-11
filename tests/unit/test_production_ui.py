from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.providers.llm.fake import DeterministicFakeLLMProvider
from app.schemas.production_settings import ProductionSettings
from app.services.scripts.regenerator import regenerate_section


@dataclass
class _TaskResult:
    id: str = "task-ui"


def _topic(session: Session) -> Topic:
    channel = Channel(name="ui-channel")
    session.add(channel)
    session.flush()
    topic = Topic(
        channel_id=channel.id,
        title="UIテスト企画",
        source_type="manual",
        source_ref="ui-topic",
    )
    session.add(topic)
    session.commit()
    return topic


def _script_body() -> dict:
    return {
        "title_candidates": ["タイトル"],
        "target_audience": "初心者",
        "viewer_problem": "課題",
        "promised_outcome": "理解できる",
        "hook": "導入",
        "sections": [
            {
                "heading": "最初",
                "narration": "最初の説明です。",
                "visual_instruction": "背景A",
                "evidence_ids": [],
                "dialogue": [],
            },
            {
                "heading": "次",
                "narration": "次の説明です。",
                "visual_instruction": "背景B",
                "evidence_ids": [],
                "dialogue": [],
            },
        ],
        "conclusion": "まとめ",
        "call_to_action": "登録",
        "description": "説明",
        "tags": ["test"],
        "chapters": ["最初", "次"],
    }


def _csrf(client: TestClient, url: str) -> str:
    return client.get(url).cookies["csrf_token"]


def _editable_project(session: Session, topic: Topic) -> VideoProject:
    script = Script(
        topic_id=topic.id,
        version=1,
        title="タイトル",
        body=_script_body(),
        source_manifest={},
        status="reviewed",
    )
    session.add(script)
    session.flush()
    project = VideoProject(
        topic_id=topic.id,
        script_id=script.id,
        status="SCRIPT_REVIEWED",
        generation=1,
    )
    session.add(project)
    session.commit()
    return project


def test_topic_ui_saves_production_settings(client: TestClient, db_session: Session) -> None:
    topic = _topic(db_session)
    page = client.get(f"/topics/{topic.id}")
    assert "動画の制作設定" in page.text
    assert "標準8分" in page.text
    token = page.cookies["csrf_token"]
    response = client.post(
        f"/topics/{topic.id}/production-settings",
        data={
            "csrf_token": token,
            "preset": "standard_3min",
            "target_duration_seconds": "999",
            "min_duration_seconds": "998",
            "max_duration_seconds": "1000",
            "min_sections": "1",
            "max_sections": "2",
            "speaking_rate": "1.25",
            "script_template": "comparison",
            "tone": "明るく簡潔",
            "dialogue_ratio": "0.4",
            "target_character_count": "",
            "intent": "save",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    project = db_session.query(VideoProject).filter(VideoProject.topic_id == topic.id).one()
    assert project.production_settings["target_duration_seconds"] == 180
    assert project.production_settings["script_template"] == "comparison"
    assert project.production_settings["speaking_rate"] == 1.25


def test_video_project_ui_edits_and_reorders_sections(
    client: TestClient, db_session: Session
) -> None:
    topic = _topic(db_session)
    project = _editable_project(db_session, topic)
    page = client.get(f"/video-projects/{project.id}")
    assert "推定" in page.text
    assert "この部分だけAI再生成" in page.text
    token = page.cookies["csrf_token"]

    response = client.post(
        f"/video-projects/{project.id}/script/sections/0/update",
        data={
            "csrf_token": token,
            "heading": "更新済み",
            "narration": "更新後のナレーションです。",
            "visual_instruction": "更新背景",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    db_session.refresh(project)
    edited = db_session.get(Script, project.script_id)
    assert edited is not None and edited.version == 2
    assert edited.body["sections"][0]["heading"] == "更新済み"

    token = _csrf(client, f"/video-projects/{project.id}")
    response = client.post(
        f"/video-projects/{project.id}/script/sections/1/move",
        data={"csrf_token": token, "direction": "up"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db_session.refresh(project)
    reordered = db_session.get(Script, project.script_id)
    assert reordered is not None and reordered.version == 3
    assert reordered.body["sections"][0]["heading"] == "次"


def test_section_regeneration_is_dispatched(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    topic = _topic(db_session)
    project = _editable_project(db_session, topic)
    captured: list[tuple[str, int, str]] = []
    monkeypatch.setattr(
        "app.web.video_projects.regenerate_section_task.delay",
        lambda project_id, index, instruction: (
            captured.append((project_id, index, instruction)) or _TaskResult()
        ),
    )
    token = _csrf(client, f"/video-projects/{project.id}")
    response = client.post(
        f"/video-projects/{project.id}/script/sections/0/regenerate",
        data={"csrf_token": token, "instruction": "具体例を追加"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert captured == [(project.id, 0, "具体例を追加")]


def test_regenerate_section_creates_new_script_version(db_session: Session) -> None:
    topic = _topic(db_session)
    project = _editable_project(db_session, topic)
    source = db_session.get(Script, project.script_id)
    assert source is not None

    edited = asyncio.run(
        regenerate_section(
            db_session,
            script=source,
            section_index=0,
            instruction="初心者向けに改善",
            production_settings=ProductionSettings(),
            provider=DeterministicFakeLLMProvider(),
            dialogue_enabled=False,
        )
    )

    assert edited.version == 2
    assert edited.id != source.id
    assert "改善しました" in edited.body["sections"][0]["narration"]
