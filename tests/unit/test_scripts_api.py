from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.evidence import Evidence
from app.models.topic import Topic


def _make_topic_with_evidence(db_session: Session) -> Topic:
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

    db_session.add(
        Evidence(
            topic_id=topic.id,
            source_url="https://example.com/a",
            source_title="出典A",
            claim="市場は年10%成長している",
            excerpt_hash="abc123",
        )
    )
    db_session.commit()
    return topic


def test_generate_script_then_get_via_api(client: TestClient, db_session: Session) -> None:
    topic = _make_topic_with_evidence(db_session)

    response = client.post(f"/api/topics/{topic.id}/generate-script")
    assert response.status_code == 201
    body = response.json()
    assert body["topic_id"] == topic.id
    assert body["status"] == "draft"
    assert "findings" in body

    script_id = body["id"]
    get_response = client.get(f"/api/scripts/{script_id}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == script_id


def test_generate_script_accepts_production_settings(
    client: TestClient, db_session: Session
) -> None:
    topic = _make_topic_with_evidence(db_session)

    response = client.post(
        f"/api/topics/{topic.id}/generate-script",
        json={
            "preset": "custom",
            "video_format": "custom",
            "target_duration_seconds": 90,
            "min_duration_seconds": 75,
            "max_duration_seconds": 105,
            "min_sections": 2,
            "max_sections": 4,
            "script_template": "comparison",
        },
    )

    assert response.status_code == 201
    manifest = response.json()["source_manifest"]
    assert manifest["production_settings"]["target_duration_seconds"] == 90
    assert manifest["production_settings"]["script_template"] == "comparison"


def test_generate_script_rejects_invalid_production_settings(
    client: TestClient, db_session: Session
) -> None:
    topic = _make_topic_with_evidence(db_session)

    response = client.post(
        f"/api/topics/{topic.id}/generate-script",
        json={"speaking_rate": 0},
    )

    assert response.status_code == 422


def test_generate_script_topic_not_found_via_api(client: TestClient) -> None:
    response = client.post("/api/topics/does-not-exist/generate-script")
    assert response.status_code == 404


def test_get_script_not_found_via_api(client: TestClient) -> None:
    response = client.get("/api/scripts/does-not-exist")
    assert response.status_code == 404
