"""Phase 6の統計・コメント・Insight同期オーケストレーター。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.comment import Comment
from app.models.insight import Insight
from app.models.publication import Publication
from app.models.video_metric_daily import VideoMetricDaily
from app.providers.youtube.base import YouTubeProvider
from app.services.analytics.sync import sync_video_metrics
from app.services.comments.sync import sync_comments
from app.services.feedback.insights import generate_publication_insights


@dataclass(frozen=True)
class FeedbackSyncResult:
    metric: VideoMetricDaily
    comments: list[Comment]
    insights: list[Insight]


async def sync_publication_feedback(
    session: Session, *, publication_id: str, provider: YouTubeProvider
) -> FeedbackSyncResult:
    """1つのPublicationについて指標・コメント・Insightをまとめて同期する。"""
    metric = await sync_video_metrics(session, publication_id=publication_id, provider=provider)
    comments = await sync_comments(session, publication_id=publication_id, provider=provider)
    insights = generate_publication_insights(session, publication_id=publication_id)
    session.flush()
    return FeedbackSyncResult(metric=metric, comments=comments, insights=insights)


async def sync_all_completed_publications_feedback(
    session: Session, *, provider: YouTubeProvider
) -> list[FeedbackSyncResult]:
    """アップロード完了済みPublicationを一括同期する。

    MVPではFake YouTube投稿直後のprivate動画でも分析デモができるよう、
    `published_at` ではなく `upload_status="completed"` と `youtube_video_id IS NOT NULL`
    を対象条件にする。
    """
    publications = (
        session.query(Publication)
        .filter(Publication.upload_status == "completed", Publication.youtube_video_id.isnot(None))
        .order_by(Publication.created_at.asc())
        .all()
    )
    results: list[FeedbackSyncResult] = []
    for publication in publications:
        results.append(
            await sync_publication_feedback(
                session, publication_id=publication.id, provider=provider
            )
        )
    return results
