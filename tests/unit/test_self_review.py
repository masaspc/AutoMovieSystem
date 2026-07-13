"""投稿後セルフレビュー(指標・維持率・コメント→Insight)の検証。"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.comment import Comment
from app.models.insight import Insight
from app.models.job_run import JobRun
from app.models.publication import Publication
from app.models.script import Script
from app.models.topic import Topic
from app.models.usage_record import UsageRecord
from app.models.video_metric_daily import VideoMetricDaily
from app.models.video_project import VideoProject
from app.providers.llm.base import StructuredLLMResult
from app.providers.llm.fake import DeterministicFakeLLMProvider
from app.services.feedback.self_review import (
    SELF_REVIEW_INSIGHT_TYPE,
    collect_recent_lessons,
    run_self_review,
)


class _RecordingProvider:
    def __init__(self) -> None:
        self._delegate = DeterministicFakeLLMProvider()
        self.user_prompts: list[str] = []

    async def generate_structured(
        self,
        *,
        operation: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[BaseModel],
        model_policy: str,
        idempotency_key: str,
    ) -> StructuredLLMResult:
        self.user_prompts.append(user_prompt)
        return await self._delegate.generate_structured(
            operation=operation,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=response_schema,
            model_policy=model_policy,
            idempotency_key=idempotency_key,
        )


def _publication_with_metric(
    db_session: Session,
    *,
    channel_name: str = "ch",
    metric_date: date = date(2026, 7, 12),
) -> tuple[Publication, Channel]:
    channel = Channel(name=channel_name)
    db_session.add(channel)
    db_session.flush()
    topic = Topic(
        channel_id=channel.id,
        title="セルフレビュー対象",
        source_type="manual",
        source_ref=f"source-{channel.id}",
    )
    db_session.add(topic)
    db_session.flush()
    script = Script(
        topic_id=topic.id,
        version=1,
        title="タイトル",
        body={"sections": [{"heading": "導入"}, {"heading": "本編"}]},
        source_manifest={},
        status="reviewed",
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
        youtube_video_id=f"yt-{channel.id}",
        title="タイトル",
        description="説明",
        tags=[],
        privacy_status="private",
        idempotency_key=f"upload-{channel.id}",
        upload_status="completed",
    )
    db_session.add(publication)
    db_session.flush()
    db_session.add(
        VideoMetricDaily(
            publication_id=publication.id,
            metric_date=metric_date,
            views=1_200,
            watch_minutes=310.0,
            average_view_duration=42.0,
            average_view_percentage=0.48,
            impressions=10_000,
            ctr=0.06,
            likes=80,
            comments_count=1,
            subscribers_gained=7,
        )
    )
    db_session.commit()
    return publication, channel


def test_run_self_review_persists_lessons_and_usage(db_session: Session) -> None:
    publication, _ = _publication_with_metric(db_session)
    provider = DeterministicFakeLLMProvider()

    insights = asyncio.run(
        run_self_review(db_session, publication_id=publication.id, provider=provider)
    )
    db_session.commit()

    assert len(insights) == 2
    assert all(insight.insight_type == SELF_REVIEW_INSIGHT_TYPE for insight in insights)
    assert all(insight.source_id == publication.id for insight in insights)
    assert all(insight.recommended_action for insight in insights)
    assert all(insight.evidence["metric_date"] == "2026-07-12" for insight in insights)
    usage = db_session.query(UsageRecord).one()
    assert usage.operation == "self_review"
    assert usage.model == "fake-mid"
    job = db_session.query(JobRun).one()
    assert job.idempotency_key == f"self_review:{publication.id}:2026-07-12"
    assert job.status == "succeeded"


def test_self_review_prompt_contains_retention_and_comment_summary(db_session: Session) -> None:
    publication, _ = _publication_with_metric(db_session)
    db_session.add(
        Insight(
            source_type="publication",
            source_id=publication.id,
            insight_type="retention_scene_dip",
            source_ref=f"retention:{publication.id}:section:1",
            finding="本編で離脱が増えた",
            evidence={"section_index": 1, "audience_watch_ratio": 0.31},
            confidence=0.8,
            recommended_action="本編の画面を早く切り替える",
            human_review_reason="映像確認が必要",
        )
    )
    db_session.add(
        Comment(
            publication_id=publication.id,
            youtube_comment_id="comment-1",
            author_hash="author",
            text="分かりやすかった",
            published_at=datetime(2026, 7, 12, 12, 0),
            category="POSITIVE",
            sentiment="positive",
        )
    )
    db_session.commit()
    provider = _RecordingProvider()

    asyncio.run(run_self_review(db_session, publication_id=publication.id, provider=provider))

    payload = json.loads(provider.user_prompts[0])
    assert payload["latest_metric"]["ctr_ratio"] == 0.06
    assert payload["latest_metric"]["average_view_percentage"] == 0.48
    assert payload["retention_scene_analysis"][0]["scene"]["section_index"] == 1
    assert payload["comment_category_counts"] == {"POSITIVE": 1}


def test_run_self_review_is_idempotent_for_same_metric_date(db_session: Session) -> None:
    publication, _ = _publication_with_metric(db_session)
    provider = _RecordingProvider()

    first = asyncio.run(
        run_self_review(db_session, publication_id=publication.id, provider=provider)
    )
    db_session.commit()
    second = asyncio.run(
        run_self_review(db_session, publication_id=publication.id, provider=provider)
    )
    db_session.commit()

    assert [item.id for item in second] == [item.id for item in first]
    assert len(provider.user_prompts) == 1
    assert db_session.query(Insight).count() == 2
    assert db_session.query(UsageRecord).count() == 1


def test_new_metric_date_allows_another_self_review(db_session: Session) -> None:
    publication, _ = _publication_with_metric(db_session)
    provider = _RecordingProvider()
    asyncio.run(run_self_review(db_session, publication_id=publication.id, provider=provider))
    db_session.commit()
    db_session.add(
        VideoMetricDaily(
            publication_id=publication.id,
            metric_date=date(2026, 7, 13),
            views=1_500,
            ctr=0.07,
            average_view_percentage=0.52,
        )
    )
    db_session.commit()

    latest = asyncio.run(
        run_self_review(db_session, publication_id=publication.id, provider=provider)
    )
    db_session.commit()

    assert len(latest) == 2
    assert all(item.evidence["metric_date"] == "2026-07-13" for item in latest)
    assert db_session.query(Insight).count() == 4
    assert db_session.query(UsageRecord).count() == 2


def test_explicit_metric_date_reviews_that_day_even_when_newer_metric_exists(
    db_session: Session,
) -> None:
    publication, _ = _publication_with_metric(db_session)
    db_session.add(
        VideoMetricDaily(
            publication_id=publication.id,
            metric_date=date(2026, 7, 13),
            views=9_999,
            ctr=0.09,
            average_view_percentage=0.7,
        )
    )
    db_session.commit()

    insights = asyncio.run(
        run_self_review(
            db_session,
            publication_id=publication.id,
            provider=DeterministicFakeLLMProvider(),
            metric_date=date(2026, 7, 12),
        )
    )

    assert len(insights) == 2
    assert all(item.evidence["metric_date"] == "2026-07-12" for item in insights)
    assert db_session.query(JobRun).one().idempotency_key.endswith(":2026-07-12")


def test_run_self_review_skips_publication_without_metrics(db_session: Session) -> None:
    publication, _ = _publication_with_metric(db_session)
    db_session.query(VideoMetricDaily).delete()
    db_session.commit()

    insights = asyncio.run(
        run_self_review(
            db_session,
            publication_id=publication.id,
            provider=DeterministicFakeLLMProvider(),
        )
    )

    assert insights == []
    assert db_session.query(JobRun).count() == 0
    assert db_session.query(UsageRecord).count() == 0


def test_collect_recent_lessons_is_channel_scoped_and_limited(db_session: Session) -> None:
    publication_a, channel_a = _publication_with_metric(db_session, channel_name="a")
    publication_b, _ = _publication_with_metric(db_session, channel_name="b")
    provider = DeterministicFakeLLMProvider()
    asyncio.run(run_self_review(db_session, publication_id=publication_a.id, provider=provider))
    asyncio.run(run_self_review(db_session, publication_id=publication_b.id, provider=provider))
    db_session.commit()

    lessons = collect_recent_lessons(db_session, channel_id=channel_a.id, limit=1)

    assert len(lessons) == 1
    assert lessons[0].source_id == publication_a.id
    assert collect_recent_lessons(db_session, channel_id=channel_a.id, limit=0) == []
