from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.channel import Channel


def _make_channel(db_session: Session) -> Channel:
    channel = Channel(name="tech-channel")
    db_session.add(channel)
    db_session.commit()
    return channel


def test_create_topic_then_score_via_api(client: TestClient, db_session: Session) -> None:
    channel = _make_channel(db_session)

    create_response = client.post(
        "/api/topics",
        json={
            "channel_id": channel.id,
            "title": "APIから作った企画",
            "description": "説明",
            "client_key": "api-client-key-1",
        },
    )
    assert create_response.status_code == 201
    topic = create_response.json()
    assert topic["status"] == "created"
    assert topic["channel_id"] == channel.id

    score_response = client.post(f"/api/topics/{topic['id']}/score")
    assert score_response.status_code == 200
    scored = score_response.json()
    assert scored["status"] == "scored"
    assert "total_score" in scored

    list_response = client.get("/api/topics", params={"channel_id": channel.id})
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1


def test_create_topic_get_or_create_via_api(client: TestClient, db_session: Session) -> None:
    channel = _make_channel(db_session)
    payload = {
        "channel_id": channel.id,
        "title": "企画A",
        "description": None,
        "client_key": "dup-key",
    }

    first = client.post("/api/topics", json=payload)
    second = client.post("/api/topics", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_import_csv_via_api(client: TestClient, db_session: Session) -> None:
    channel = _make_channel(db_session)
    csv_text = "title,description,source_url\n企画X,説明X,https://example.com/x\n"

    response = client.post(
        "/api/topics/import-csv",
        json={"channel_id": channel.id, "csv_text": csv_text},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert body["skipped"] == 0


def test_score_topic_not_found_via_api(client: TestClient) -> None:
    response = client.post("/api/topics/does-not-exist/score")
    assert response.status_code == 404
