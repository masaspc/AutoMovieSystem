from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.topic import Topic
from app.services.topics.importer import (
    InvalidCsvError,
    create_manual_topic,
    import_topics_csv,
)

SAMPLE_CSV_PATH = Path(__file__).resolve().parents[2] / "sample_data" / "topics_sample.csv"


def _make_channel(db_session: Session) -> Channel:
    channel = Channel(name="tech-channel")
    db_session.add(channel)
    db_session.flush()
    return channel


def test_create_manual_topic_get_or_create_by_client_key(db_session: Session) -> None:
    channel = _make_channel(db_session)

    first = create_manual_topic(
        db_session,
        channel_id=channel.id,
        title="企画A",
        description="説明A",
        client_key="client-key-1",
    )
    second = create_manual_topic(
        db_session,
        channel_id=channel.id,
        title="企画A(別内容でも同じキー)",
        description="別の説明",
        client_key="client-key-1",
    )

    assert first.id == second.id
    count = db_session.query(Topic).filter(Topic.channel_id == channel.id).count()
    assert count == 1


def test_create_manual_topic_different_keys_create_separate_topics(db_session: Session) -> None:
    channel = _make_channel(db_session)

    create_manual_topic(
        db_session, channel_id=channel.id, title="企画A", description=None, client_key="key-a"
    )
    create_manual_topic(
        db_session, channel_id=channel.id, title="企画B", description=None, client_key="key-b"
    )

    count = db_session.query(Topic).filter(Topic.channel_id == channel.id).count()
    assert count == 2


def test_import_topics_csv_sample_file(db_session: Session) -> None:
    channel = _make_channel(db_session)
    csv_text = SAMPLE_CSV_PATH.read_text(encoding="utf-8")

    result = import_topics_csv(db_session, channel_id=channel.id, csv_text=csv_text)

    assert result.created == 5
    assert result.skipped == 0
    assert result.total_rows == 5
    assert db_session.query(Topic).filter(Topic.channel_id == channel.id).count() == 5


def test_import_topics_csv_twice_is_idempotent(db_session: Session) -> None:
    channel = _make_channel(db_session)
    csv_text = SAMPLE_CSV_PATH.read_text(encoding="utf-8")

    first = import_topics_csv(db_session, channel_id=channel.id, csv_text=csv_text)
    second = import_topics_csv(db_session, channel_id=channel.id, csv_text=csv_text)

    assert first.created == 5
    assert second.created == 0
    assert second.skipped == 5
    assert db_session.query(Topic).filter(Topic.channel_id == channel.id).count() == 5


def test_import_topics_csv_missing_column_raises(db_session: Session) -> None:
    channel = _make_channel(db_session)
    csv_text = "title,description\n企画A,説明A\n"

    with pytest.raises(InvalidCsvError):
        import_topics_csv(db_session, channel_id=channel.id, csv_text=csv_text)


def test_import_topics_csv_empty_title_raises(db_session: Session) -> None:
    channel = _make_channel(db_session)
    csv_text = "title,description,source_url\n,説明A,https://example.com\n"

    with pytest.raises(InvalidCsvError):
        import_topics_csv(db_session, channel_id=channel.id, csv_text=csv_text)


def test_import_topics_csv_reordered_headers(db_session: Session) -> None:
    channel = _make_channel(db_session)
    csv_text = "source_url,title,description\nhttps://example.com,企画X,説明X\n"

    result = import_topics_csv(db_session, channel_id=channel.id, csv_text=csv_text)

    assert result.created == 1
