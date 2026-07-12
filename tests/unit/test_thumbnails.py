"""シリーズ統一サムネイル自動生成の単体テスト。実描画(Pillow)のみで外部APIは呼ばない。"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.models.asset import Asset
from app.models.channel import Channel
from app.models.episode_plan import EpisodePlan
from app.models.script import Script
from app.models.series_plan import SeriesPlan
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.schemas.script_content import ScriptContent
from app.services.media import thumbnails
from app.services.media.branding import SeriesBranding, resolve_branding


def _make_channel(db_session: Session, *, name: str = "channel-1") -> Channel:
    channel = Channel(name=name)
    db_session.add(channel)
    db_session.flush()
    return channel


def _make_topic(db_session: Session, channel: Channel, *, source_ref: str = "topic-1") -> Topic:
    topic = Topic(
        channel_id=channel.id,
        title="台本タイトル",
        source_type="manual",
        source_ref=source_ref,
    )
    db_session.add(topic)
    db_session.flush()
    return topic


def _make_script(
    db_session: Session, topic: Topic, *, thumbnail_texts: list[str] | None = None
) -> Script:
    body = {
        "title_candidates": ["知らないと損するPython入門"],
        "target_audience": "初心者",
        "viewer_problem": "何から学べばよいか分からない",
        "promised_outcome": "次の一歩が分かる",
        "hook": "実は多くの人が誤解しています",
        "sections": [
            {
                "heading": "導入",
                "narration": "今日のテーマを紹介します。",
                "visual_instruction": "タイトル表示",
                "evidence_ids": [],
                "dialogue": [{"speaker": "zundamon", "text": "はじめるのだ。", "emotion": "happy"}],
            }
        ],
        "conclusion": "まとめ",
        "call_to_action": "登録してください",
        "description": "説明",
        "tags": ["python"],
        "chapters": ["導入"],
    }
    if thumbnail_texts is not None:
        body["thumbnail_texts"] = thumbnail_texts
    script = Script(
        topic_id=topic.id,
        version=1,
        title="台本タイトル",
        body=body,
        source_manifest={},
        status="reviewed",
    )
    db_session.add(script)
    db_session.flush()
    return script


def _make_project(db_session: Session, topic: Topic, script: Script) -> VideoProject:
    project = VideoProject(
        topic_id=topic.id, script_id=script.id, status="SCRIPT_REVIEWED", generation=1
    )
    db_session.add(project)
    db_session.flush()
    return project


# --- resolve_branding ---------------------------------------------------------------


def test_resolve_branding_same_series_name_is_deterministic() -> None:
    first = resolve_branding("入門講座シリーズ", None)
    second = resolve_branding("入門講座シリーズ", None)
    assert first == second
    assert first.accent_color.startswith("#")
    assert first.secondary_color.startswith("#")


def test_resolve_branding_prefers_stored_settings() -> None:
    stored = {
        "accent_color": "#123456",
        "secondary_color": "#654321",
        "text_color": "#FFFFFF",
        "template": "clean",
    }
    branding = resolve_branding("任意の名前", stored)
    assert branding == SeriesBranding.model_validate(stored)


def test_resolve_branding_different_names_can_yield_different_palettes() -> None:
    colors = {resolve_branding(f"シリーズ{i}", None).accent_color for i in range(8)}
    # 8色パレットに対し8個の異なる名前を渡せば複数の異なる色が選ばれるはず(単一色への
    # 縮退がないことの簡易検証。決定的ハッシュのため厳密な分布保証はしない)。
    assert len(colors) > 1


# --- generate_thumbnail_candidates --------------------------------------------------


def test_generate_thumbnail_candidates_creates_three_assets_with_files(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GENERATED_DIR", str(tmp_path))
    channel = _make_channel(db_session)
    topic = _make_topic(db_session, channel)
    script = _make_script(
        db_session, topic, thumbnail_texts=["えっ、5分で!?", "初心者の9割が誤解", "完全ガイド"]
    )
    project = _make_project(db_session, topic, script)

    assets = thumbnails.generate_thumbnail_candidates(db_session, video_project_id=project.id)

    assert len(assets) == 3
    roles = sorted(a.role for a in assets)
    assert roles == [
        "thumbnail:candidate:0",
        "thumbnail:candidate:1",
        "thumbnail:candidate:2",
    ]
    for asset in assets:
        path = Path(asset.file_path)
        assert path.exists()
        assert path.stat().st_size > 0
        assert asset.meta["spec_version"] == thumbnails.THUMBNAIL_SPEC_VERSION


def test_generate_thumbnail_candidates_is_idempotent_checksum_stable(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GENERATED_DIR", str(tmp_path))
    channel = _make_channel(db_session)
    topic = _make_topic(db_session, channel)
    script = _make_script(db_session, topic)
    project = _make_project(db_session, topic, script)

    first = thumbnails.generate_thumbnail_candidates(db_session, video_project_id=project.id)
    db_session.flush()
    checksums_first = sorted((a.role, a.checksum) for a in first)

    second = thumbnails.generate_thumbnail_candidates(db_session, video_project_id=project.id)
    checksums_second = sorted((a.role, a.checksum) for a in second)

    assert checksums_first == checksums_second
    assert (
        db_session.query(Asset)
        .filter(
            Asset.video_project_id == project.id,
            Asset.role.like(f"{thumbnails.THUMBNAIL_ROLE_PREFIX}%"),
        )
        .count()
        == 3
    )


def test_generate_thumbnail_candidates_with_series_context_succeeds(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GENERATED_DIR", str(tmp_path))
    channel = _make_channel(db_session)
    series = SeriesPlan(
        channel_id=channel.id,
        name="Python入門シリーズ",
        target_audience="初心者",
        starting_knowledge="なし",
        final_goal="基礎を身につける",
        planned_episode_count=10,
    )
    db_session.add(series)
    db_session.flush()

    topic = _make_topic(db_session, channel)
    episode = EpisodePlan(
        series_plan_id=series.id,
        position=3,
        title="第3回",
        summary="要約",
        target_duration_seconds=300,
        topic_id=topic.id,
    )
    db_session.add(episode)
    db_session.flush()

    script = _make_script(db_session, topic)
    project = _make_project(db_session, topic, script)

    assets = thumbnails.generate_thumbnail_candidates(db_session, video_project_id=project.id)

    assert len(assets) == 3
    for asset in assets:
        assert Path(asset.file_path).exists()


def test_generate_thumbnail_candidates_without_character_assets_still_succeeds(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CHARACTER_RENDER_ENABLED=trueでも立ち絵素材が存在しなければテキストのみで成功する。"""
    monkeypatch.setenv("GENERATED_DIR", str(tmp_path))
    monkeypatch.setenv("CHARACTER_RENDER_ENABLED", "true")
    monkeypatch.setenv("CHARACTER_ASSETS_DIR", str(tmp_path / "no-such-characters"))

    channel = _make_channel(db_session)
    topic = _make_topic(db_session, channel)
    script = _make_script(db_session, topic)
    project = _make_project(db_session, topic, script)

    assets = thumbnails.generate_thumbnail_candidates(db_session, video_project_id=project.id)

    assert len(assets) == 3
    for asset in assets:
        assert Path(asset.file_path).exists()


# --- select_thumbnail ----------------------------------------------------------------


def test_select_thumbnail_upserts_role_and_copies_file(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GENERATED_DIR", str(tmp_path))
    channel = _make_channel(db_session)
    topic = _make_topic(db_session, channel)
    script = _make_script(db_session, topic)
    project = _make_project(db_session, topic, script)

    thumbnails.generate_thumbnail_candidates(db_session, video_project_id=project.id)

    selected = thumbnails.select_thumbnail(
        db_session, video_project_id=project.id, candidate_index=1
    )

    assert selected.role == thumbnails.THUMBNAIL_ROLE_SELECTED
    assert Path(selected.file_path).exists()
    assert selected.meta["candidate_index"] == 1

    assert (
        db_session.query(Asset)
        .filter(
            Asset.video_project_id == project.id,
            Asset.role == thumbnails.THUMBNAIL_ROLE_SELECTED,
        )
        .count()
        == 1
    )


def test_select_thumbnail_missing_candidate_raises(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GENERATED_DIR", str(tmp_path))
    channel = _make_channel(db_session)
    topic = _make_topic(db_session, channel)
    script = _make_script(db_session, topic)
    project = _make_project(db_session, topic, script)

    with pytest.raises(thumbnails.ThumbnailNotFoundError):
        thumbnails.select_thumbnail(db_session, video_project_id=project.id, candidate_index=0)


# --- ScriptContent backward compatibility --------------------------------------------


def test_script_content_without_thumbnail_texts_loads_with_empty_default() -> None:
    legacy_body = {
        "title_candidates": ["旧台本"],
        "target_audience": "初心者",
        "viewer_problem": "問題",
        "promised_outcome": "成果",
        "hook": "フック",
        "sections": [],
        "conclusion": "まとめ",
        "call_to_action": "CTA",
        "description": "説明",
        "tags": [],
        "chapters": [],
    }
    content = ScriptContent.model_validate(legacy_body)
    assert content.thumbnail_texts == []
