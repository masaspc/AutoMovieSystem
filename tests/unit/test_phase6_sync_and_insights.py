from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.comment import Comment
from app.models.insight import Insight
from app.models.publication import Publication
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_metric_daily import VideoMetricDaily
from app.models.video_project import VideoProject
from app.providers.youtube.base import CommentData, VideoStatistics
from app.providers.youtube.fake import FakeYouTubeProvider
from app.services.analytics.sync import sync_video_metrics
from app.services.comments.sync import sync_comments
from app.services.feedback.insights import (
    FeedbackRuleConfig,
    generate_comment_insights,
    generate_metric_insights,
)


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

    project = VideoProject(
        topic_id=topic.id,
        script_id=script.id,
        status="PUBLISHED",
        generation=1,
    )
    db_session.add(project)
    db_session.flush()

    publication = Publication(
        video_project_id=project.id,
        youtube_video_id="yt-1",
        title="title",
        description="desc",
        tags=[],
        privacy_status="private",
        idempotency_key="upload:1",
        upload_status="completed",
    )
    db_session.add(publication)
    db_session.flush()
    return publication


def test_sync_video_metrics_upserts_daily_row(db_session: Session) -> None:
    publication = _make_publication(db_session)
    provider = FakeYouTubeProvider()
    provider.seed_statistics(
        VideoStatistics(
            youtube_video_id="yt-1",
            view_count=123,
            like_count=10,
            comment_count=4,
            collected_at=datetime(2026, 7, 5, tzinfo=UTC),
        )
    )

    first = asyncio.run(
        sync_video_metrics(db_session, publication_id=publication.id, provider=provider)
    )
    second = asyncio.run(
        sync_video_metrics(db_session, publication_id=publication.id, provider=provider)
    )

    assert first.id == second.id
    assert second.views == 123
    assert second.likes == 10
    assert second.comments_count == 4
    assert db_session.query(VideoMetricDaily).count() == 1


def test_sync_comments_classifies_and_is_idempotent(db_session: Session) -> None:
    publication = _make_publication(db_session)
    provider = FakeYouTubeProvider()
    provider.seed_comments(
        "yt-1",
        [
            CommentData(
                youtube_comment_id="c1",
                author_display_name="alice",
                text="次回はRedisの比較を動画にして",
                published_at=datetime(2026, 7, 5, tzinfo=UTC),
                like_count=2,
            )
        ],
    )

    first = asyncio.run(sync_comments(db_session, publication_id=publication.id, provider=provider))
    second = asyncio.run(
        sync_comments(db_session, publication_id=publication.id, provider=provider)
    )

    assert first[0].id == second[0].id
    assert second[0].category == "NEXT_TOPIC_REQUEST"
    assert second[0].requires_response is True
    assert db_session.query(Comment).count() == 1


def test_sync_comments_marks_missing_comments_deleted(db_session: Session) -> None:
    publication = _make_publication(db_session)
    provider = FakeYouTubeProvider()
    provider.seed_comments(
        "yt-1",
        [
            CommentData("c1", "alice", "良い動画でした", datetime(2026, 7, 5, tzinfo=UTC)),
            CommentData("c2", "bob", "次回はRedisを解説して", datetime(2026, 7, 5, tzinfo=UTC)),
        ],
    )
    asyncio.run(sync_comments(db_session, publication_id=publication.id, provider=provider))

    provider.seed_comments(
        "yt-1",
        [CommentData("c2", "bob", "次回はRedisを解説して", datetime(2026, 7, 5, tzinfo=UTC))],
    )
    asyncio.run(sync_comments(db_session, publication_id=publication.id, provider=provider))

    deleted = db_session.query(Comment).filter(Comment.youtube_comment_id == "c1").one()
    visible = db_session.query(Comment).filter(Comment.youtube_comment_id == "c2").one()
    assert deleted.moderation_status == "deleted"
    assert visible.moderation_status == "visible"


def test_generate_comment_insights_creates_topic_candidate(db_session: Session) -> None:
    publication = _make_publication(db_session)
    provider = FakeYouTubeProvider()
    requested = "次回はRedisの比較を動画にして"
    provider.seed_comments(
        "yt-1",
        [
            CommentData(f"c{i}", f"user-{i}", requested, datetime(2026, 7, 5, tzinfo=UTC))
            for i in range(3)
        ],
    )
    asyncio.run(sync_comments(db_session, publication_id=publication.id, provider=provider))

    insights = generate_comment_insights(db_session, publication_id=publication.id)

    assert len(insights) == 1
    assert insights[0].insight_type == "next_topic_request"
    assert insights[0].source_ref
    assert insights[0].evidence["count"] == 3
    assert insights[0].human_review_reason
    assert db_session.query(Insight).count() == 1
    assert db_session.query(Topic).filter(Topic.source_type == "comment").count() == 1


def test_generate_comment_insights_handles_problem_and_comparison_rules(
    db_session: Session,
) -> None:
    publication = _make_publication(db_session)
    provider = FakeYouTubeProvider()
    provider.seed_comments(
        "yt-1",
        [
            CommentData("p1", "u1", "エラーE100で動かない", datetime(2026, 7, 5, tzinfo=UTC)),
            CommentData("p2", "u2", "エラーE100で動かない", datetime(2026, 7, 5, tzinfo=UTC)),
            CommentData("q1", "u3", "AとBの比較をお願いします", datetime(2026, 7, 5, tzinfo=UTC)),
            CommentData("q2", "u4", "AとBの比較をお願いします", datetime(2026, 7, 5, tzinfo=UTC)),
        ],
    )
    asyncio.run(sync_comments(db_session, publication_id=publication.id, provider=provider))

    insights = generate_comment_insights(
        db_session,
        publication_id=publication.id,
        config=FeedbackRuleConfig(comparison_request_threshold=2),
    )

    insight_types = {insight.insight_type for insight in insights}
    assert "problem_supplement" in insight_types
    assert "comparison_video_request" in insight_types
    assert db_session.query(Topic).filter(Topic.source_type == "comment").count() == 1


def test_generate_metric_insights_creates_improvement_suggestion(db_session: Session) -> None:
    publication = _make_publication(db_session)
    metric = VideoMetricDaily(
        publication_id=publication.id,
        metric_date=datetime(2026, 7, 5, tzinfo=UTC).date(),
        views=800,
        ctr=0.01,
        average_view_percentage=0.75,
        comments_count=40,
        subscribers_gained=20,
    )
    db_session.add(metric)
    db_session.flush()

    insights = generate_metric_insights(db_session, publication_id=publication.id)

    insight_types = {insight.insight_type for insight in insights}
    assert "title_thumbnail_improvement" in insight_types
    assert "focus_theme" in insight_types
    assert "specialist_series_candidate" in insight_types
