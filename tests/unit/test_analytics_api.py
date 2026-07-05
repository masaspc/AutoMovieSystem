from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.publication import Publication
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.providers.youtube.base import CommentData, VideoStatistics
from app.providers.youtube.fake import FakeYouTubeProvider


def _make_publication(db_session: Session) -> Publication:
    channel = Channel(name="ch")
    db_session.add(channel)
    db_session.flush()

    topic = Topic(channel_id=channel.id, title="topic", source_type="manual", source_ref="r-1")
    db_session.add(topic)
    db_session.flush()

    script = Script(
        topic_id=topic.id,
        version=1,
        title="title",
        body={"description": "desc"},
        source_manifest={},
    )
    db_session.add(script)
    db_session.flush()

    project = VideoProject(topic_id=topic.id, script_id=script.id, status="PUBLISHED", generation=1)
    db_session.add(project)
    db_session.flush()

    publication = Publication(
        video_project_id=project.id,
        youtube_video_id="yt-api-1",
        title="title",
        description="desc",
        tags=[],
        privacy_status="private",
        idempotency_key="upload:api:1",
        upload_status="completed",
    )
    db_session.add(publication)
    db_session.commit()
    return publication


def test_sync_feedback_endpoint_persists_metrics_comments_and_insights(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    publication = _make_publication(db_session)
    provider = FakeYouTubeProvider()
    provider.seed_statistics(
        VideoStatistics(
            youtube_video_id="yt-api-1",
            view_count=50,
            like_count=8,
            comment_count=3,
            collected_at=datetime(2026, 7, 5, tzinfo=UTC),
        )
    )
    provider.seed_comments(
        "yt-api-1",
        [
            CommentData(
                f"c{i}",
                f"user-{i}",
                "次回はRedisを解説して",
                datetime(2026, 7, 5, tzinfo=UTC),
            )
            for i in range(3)
        ],
    )
    monkeypatch.setattr("app.api.analytics.get_youtube_provider", lambda: provider)

    response = client.post(f"/api/publications/{publication.id}/sync-feedback")

    assert response.status_code == 200
    body = response.json()
    assert body["metric"]["views"] == 50
    assert body["comments_synced"] == 3
    assert body["insights"][0]["insight_type"] == "next_topic_request"

    comments_response = client.get(
        f"/api/publications/{publication.id}/comments?category=NEXT_TOPIC_REQUEST"
    )
    assert comments_response.status_code == 200
    assert len(comments_response.json()) == 3

    insights_response = client.get(f"/api/publications/{publication.id}/insights")
    assert insights_response.status_code == 200
    insight_types = {item["insight_type"] for item in insights_response.json()}
    assert "next_topic_request" in insight_types


def test_comments_endpoint_rejects_unknown_category(
    client: TestClient, db_session: Session
) -> None:
    publication = _make_publication(db_session)

    response = client.get(f"/api/publications/{publication.id}/comments?category=NOPE")

    assert response.status_code == 400


def test_sync_metrics_endpoint_missing_upload_returns_400(
    client: TestClient, db_session: Session
) -> None:
    publication = _make_publication(db_session)
    publication.youtube_video_id = None
    db_session.commit()

    response = client.post(f"/api/publications/{publication.id}/sync-metrics")

    assert response.status_code == 400
