"""サムネイル管理画面ルート(候補配信・選択)のテスト。実APIは呼ばない。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.asset import Asset
from app.models.channel import Channel
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.services.media import thumbnails

pytestmark = pytest.mark.usefixtures("_clear_settings_cache")


def _make_channel(db_session: Session) -> Channel:
    channel = Channel(name="channel-1")
    db_session.add(channel)
    db_session.flush()
    return channel


def _make_topic(db_session: Session, channel: Channel) -> Topic:
    topic = Topic(
        channel_id=channel.id, title="タイトル", source_type="manual", source_ref="topic-1"
    )
    db_session.add(topic)
    db_session.flush()
    return topic


def _make_script(db_session: Session, topic: Topic) -> Script:
    script = Script(
        topic_id=topic.id,
        version=1,
        title="台本タイトル",
        body={
            "title_candidates": ["台本タイトル"],
            "description": "説明",
            "sections": [{"heading": "導入", "narration": "こんにちは。", "evidence_ids": []}],
        },
        source_manifest={},
        status="reviewed",
    )
    db_session.add(script)
    db_session.flush()
    return script


def _make_project(db_session: Session, topic: Topic, script: Script) -> VideoProject:
    project = VideoProject(
        topic_id=topic.id, script_id=script.id, status="ASSETS_READY", generation=1
    )
    db_session.add(project)
    db_session.flush()
    return project


def _seed_candidates(
    db_session: Session, project: VideoProject, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GENERATED_DIR", str(tmp_path))
    for index in range(3):
        path = tmp_path / "videos" / project.id / "thumbnails" / f"candidate_{index}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"png-{index}".encode())
        db_session.add(
            Asset(
                video_project_id=project.id,
                asset_type="image",
                role=f"{thumbnails.THUMBNAIL_ROLE_PREFIX}{index}",
                file_path=str(path),
                checksum=f"checksum-{index}",
                meta={"spec_version": thumbnails.THUMBNAIL_SPEC_VERSION, "text": f"text{index}"},
            )
        )
    db_session.commit()


def test_detail_page_shows_generate_button_without_candidates(
    client: TestClient, db_session: Session
) -> None:
    """候補未生成でも(サムネイル機能導入前に素材準備済みの既存プロジェクト等)、
    詳細画面から手動生成できるボタンが表示される。"""
    channel = _make_channel(db_session)
    topic = _make_topic(db_session, channel)
    script = _make_script(db_session, topic)
    project = _make_project(db_session, topic, script)
    db_session.commit()

    response = client.get(f"/video-projects/{project.id}")
    assert response.status_code == 200
    assert "サムネイルはまだ生成されていません" in response.text
    assert f"/video-projects/{project.id}/thumbnails/generate" in response.text


def test_generate_thumbnails_route_creates_candidates(
    client: TestClient, db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = _make_channel(db_session)
    topic = _make_topic(db_session, channel)
    script = _make_script(db_session, topic)
    project = _make_project(db_session, topic, script)
    db_session.commit()
    monkeypatch.setenv("GENERATED_DIR", str(tmp_path))

    get_response = client.get(f"/video-projects/{project.id}")
    csrf_token = get_response.cookies["csrf_token"]

    response = client.post(
        f"/video-projects/{project.id}/thumbnails/generate",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert response.status_code == 303

    candidates = (
        db_session.query(Asset)
        .filter(
            Asset.video_project_id == project.id,
            Asset.role.like(f"{thumbnails.THUMBNAIL_ROLE_PREFIX}%"),
        )
        .all()
    )
    assert len(candidates) == thumbnails.THUMBNAIL_CANDIDATE_COUNT
    assert all(Path(asset.file_path).exists() for asset in candidates)


def test_detail_page_lists_thumbnail_candidates(
    client: TestClient, db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = _make_channel(db_session)
    topic = _make_topic(db_session, channel)
    script = _make_script(db_session, topic)
    project = _make_project(db_session, topic, script)
    _seed_candidates(db_session, project, tmp_path, monkeypatch)

    response = client.get(f"/video-projects/{project.id}")
    assert response.status_code == 200
    assert f"/media/{project.id}/thumbnails/candidate_0.png" in response.text
    assert f"/media/{project.id}/thumbnails/candidate_2.png" in response.text


def test_serve_thumbnail_whitelisted_filename_returns_file(
    client: TestClient, db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = _make_channel(db_session)
    topic = _make_topic(db_session, channel)
    script = _make_script(db_session, topic)
    project = _make_project(db_session, topic, script)
    _seed_candidates(db_session, project, tmp_path, monkeypatch)

    response = client.get(f"/media/{project.id}/thumbnails/candidate_0.png")
    assert response.status_code == 200
    assert response.content == b"png-0"


def test_serve_thumbnail_rejects_non_whitelisted_filename(
    client: TestClient, db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = _make_channel(db_session)
    topic = _make_topic(db_session, channel)
    script = _make_script(db_session, topic)
    project = _make_project(db_session, topic, script)
    _seed_candidates(db_session, project, tmp_path, monkeypatch)

    response = client.get(f"/media/{project.id}/thumbnails/../../etc/passwd")
    assert response.status_code == 404

    response = client.get(f"/media/{project.id}/thumbnails/not_allowed.png")
    assert response.status_code == 404


def test_select_thumbnail_without_csrf_is_forbidden(
    client: TestClient, db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = _make_channel(db_session)
    topic = _make_topic(db_session, channel)
    script = _make_script(db_session, topic)
    project = _make_project(db_session, topic, script)
    _seed_candidates(db_session, project, tmp_path, monkeypatch)

    response = client.post(
        f"/video-projects/{project.id}/thumbnail/select",
        data={"csrf_token": "invalid", "candidate_index": "1"},
    )
    assert response.status_code == 403


def test_select_thumbnail_with_valid_csrf_selects_candidate(
    client: TestClient, db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = _make_channel(db_session)
    topic = _make_topic(db_session, channel)
    script = _make_script(db_session, topic)
    project = _make_project(db_session, topic, script)
    _seed_candidates(db_session, project, tmp_path, monkeypatch)

    get_response = client.get(f"/video-projects/{project.id}")
    csrf_token = get_response.cookies["csrf_token"]

    response = client.post(
        f"/video-projects/{project.id}/thumbnail/select",
        data={"csrf_token": csrf_token, "candidate_index": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    selected = (
        db_session.query(Asset)
        .filter(
            Asset.video_project_id == project.id,
            Asset.role == thumbnails.THUMBNAIL_ROLE_SELECTED,
        )
        .one()
    )
    assert selected.meta["candidate_index"] == 1
