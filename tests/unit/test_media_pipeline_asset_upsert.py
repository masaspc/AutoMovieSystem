"""Asset upsert(修正3: role UNIQUE制約による同一性判定)の単体テスト。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.models.asset import Asset
from app.models.channel import Channel
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.services.media.pipeline import _upsert_asset


def _make_project(db_session: Session) -> VideoProject:
    channel = Channel(name="ch")
    db_session.add(channel)
    db_session.flush()

    topic = Topic(channel_id=channel.id, title="topic", source_type="manual", source_ref="r-1")
    db_session.add(topic)
    db_session.flush()

    project = VideoProject(topic_id=topic.id, status="SCRIPT_REVIEWED", generation=1)
    db_session.add(project)
    db_session.flush()
    return project


def test_upsert_asset_same_role_twice_stays_single_row(db_session: Session, tmp_path: Path) -> None:
    """同一roleで2回upsert -> 1件のまま、`one_or_none` 例外(MultipleResultsFound)は起きない。"""
    project = _make_project(db_session)
    path1 = tmp_path / "bg1.png"
    path1.write_bytes(b"a")
    path2 = tmp_path / "bg2.png"
    path2.write_bytes(b"bb")

    first = _upsert_asset(
        db_session,
        video_project_id=project.id,
        asset_type="image",
        role="background",
        file_path=path1,
        checksum="checksum-1",
        meta={"role": "background"},
    )
    db_session.flush()

    second = _upsert_asset(
        db_session,
        video_project_id=project.id,
        asset_type="image",
        role="background",
        file_path=path2,
        checksum="checksum-2",
        meta={"role": "background"},
    )
    db_session.flush()

    assert first.id == second.id
    assert (
        db_session.query(Asset)
        .filter(Asset.video_project_id == project.id, Asset.role == "background")
        .count()
        == 1
    )
    assert second.checksum == "checksum-2"
    assert second.file_path == str(path2)


def test_upsert_asset_different_roles_creates_separate_rows(
    db_session: Session, tmp_path: Path
) -> None:
    project = _make_project(db_session)
    bg_path = tmp_path / "bg.png"
    bg_path.write_bytes(b"a")
    audio_path = tmp_path / "a0.wav"
    audio_path.write_bytes(b"b")

    _upsert_asset(
        db_session,
        video_project_id=project.id,
        asset_type="image",
        role="background",
        file_path=bg_path,
        checksum="c1",
        meta={},
    )
    _upsert_asset(
        db_session,
        video_project_id=project.id,
        asset_type="audio",
        role="audio:0",
        file_path=audio_path,
        checksum="c2",
        meta={"section_index": 0},
    )
    db_session.flush()

    assert db_session.query(Asset).filter(Asset.video_project_id == project.id).count() == 2


def test_upsert_asset_violates_unique_constraint_when_role_forced_duplicate(
    db_session: Session, tmp_path: Path
) -> None:
    """UNIQUE(video_project_id, role) がDB制約として機能していることを確認する。"""
    from sqlalchemy.exc import IntegrityError

    project = _make_project(db_session)
    path1 = tmp_path / "bg1.png"
    path1.write_bytes(b"a")
    path2 = tmp_path / "bg2.png"
    path2.write_bytes(b"b")

    db_session.add(
        Asset(
            video_project_id=project.id,
            asset_type="image",
            role="background",
            file_path=str(path1),
            checksum="c1",
            meta={},
        )
    )
    db_session.flush()

    db_session.add(
        Asset(
            video_project_id=project.id,
            asset_type="image",
            role="background",
            file_path=str(path2),
            checksum="c2",
            meta={},
        )
    )
    try:
        db_session.flush()
        raise AssertionError("UNIQUE(video_project_id, role) 違反が検出されるはず")
    except IntegrityError:
        db_session.rollback()
