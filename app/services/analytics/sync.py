"""YouTube統計の同期(Phase 6 MVP)。"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.publication import Publication
from app.models.video_metric_daily import VideoMetricDaily
from app.providers.youtube.base import YouTubeProvider


class PublicationNotFoundError(ValueError):
    """Publication が存在しない場合。"""


class PublicationNotUploadedError(ValueError):
    """youtube_video_id が未設定の場合。"""


def _metric_date(now: datetime | None = None) -> date:
    now = now or datetime.now(UTC)
    return now.date()


async def sync_video_metrics(
    session: Session,
    *,
    publication_id: str,
    provider: YouTubeProvider,
    metric_date: date | None = None,
) -> VideoMetricDaily:
    """YouTubeの基本統計を取得し、Publication+日付でupsertする。"""
    publication = session.get(Publication, publication_id)
    if publication is None:
        raise PublicationNotFoundError(f"Publication not found: {publication_id}")
    if not publication.youtube_video_id:
        raise PublicationNotUploadedError(f"Publication has no youtube_video_id: {publication_id}")

    stats = await provider.get_video_statistics(youtube_video_id=publication.youtube_video_id)
    target_date = metric_date or _metric_date(stats.collected_at)

    metric = (
        session.query(VideoMetricDaily)
        .filter(
            VideoMetricDaily.publication_id == publication_id,
            VideoMetricDaily.metric_date == target_date,
        )
        .one_or_none()
    )
    if metric is None:
        metric = VideoMetricDaily(publication_id=publication_id, metric_date=target_date)
        try:
            with session.begin_nested():
                session.add(metric)
                session.flush()
        except IntegrityError:
            session.expunge(metric)
            metric = (
                session.query(VideoMetricDaily)
                .filter(
                    VideoMetricDaily.publication_id == publication_id,
                    VideoMetricDaily.metric_date == target_date,
                )
                .one()
            )

    metric.views = stats.view_count
    metric.likes = stats.like_count
    metric.comments_count = stats.comment_count
    session.flush()
    return metric
