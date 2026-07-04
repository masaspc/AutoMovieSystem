from __future__ import annotations

import asyncio

from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.evidence import Evidence
from app.models.script import Script
from app.models.topic import Topic
from app.models.usage_record import UsageRecord
from app.providers.llm.fake import DeterministicFakeLLMProvider
from app.services.scripts.generator import generate_script


def _make_topic(db_session: Session) -> Topic:
    channel = Channel(name="tech-channel")
    db_session.add(channel)
    db_session.flush()

    topic = Topic(
        channel_id=channel.id,
        title="生成AIの基礎",
        description="初心者向けの解説",
        source_type="manual",
        source_ref="client-key-1",
    )
    db_session.add(topic)
    db_session.flush()

    evidence = Evidence(
        topic_id=topic.id,
        source_url="https://example.com/a",
        source_title="出典A",
        claim="市場は年10%成長している",
        excerpt_hash="abc123",
    )
    db_session.add(evidence)
    db_session.flush()
    return topic


def test_generate_script_creates_script_with_evidence_manifest(db_session: Session) -> None:
    topic = _make_topic(db_session)
    provider = DeterministicFakeLLMProvider()

    script = asyncio.run(generate_script(db_session, topic_id=topic.id, provider=provider))
    db_session.commit()

    assert script.topic_id == topic.id
    assert script.version == 1
    assert script.status == "draft"
    assert script.title
    assert script.body["title_candidates"]
    evidence_ids = script.source_manifest["evidence_ids"]
    assert len(evidence_ids) == 1

    assert db_session.query(Script).filter(Script.topic_id == topic.id).count() == 1
    assert db_session.query(UsageRecord).count() == 1


def test_generate_script_is_idempotent(db_session: Session) -> None:
    topic = _make_topic(db_session)
    provider = DeterministicFakeLLMProvider()

    script1 = asyncio.run(generate_script(db_session, topic_id=topic.id, provider=provider))
    db_session.commit()
    script2 = asyncio.run(generate_script(db_session, topic_id=topic.id, provider=provider))
    db_session.commit()

    assert script1.id == script2.id
    assert db_session.query(Script).filter(Script.topic_id == topic.id).count() == 1
    assert db_session.query(UsageRecord).count() == 1
