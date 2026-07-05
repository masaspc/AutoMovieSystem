"""分析・コメント・Insight用JSON API(Phase 6)。"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.comment import COMMENT_CATEGORIES, Comment
from app.models.insight import Insight
from app.models.publication import Publication
from app.models.video_metric_daily import VideoMetricDaily
from app.providers.youtube.base import AuthError, QuotaExceededError, TransientAPIError
from app.providers.youtube.factory import get_youtube_provider
from app.schemas.analytics import (
    CommentResponse,
    FeedbackSyncResponse,
    InsightResponse,
    VideoMetricDailyResponse,
)
from app.services.analytics.sync import (
    PublicationNotFoundError as MetricsPublicationNotFoundError,
)
from app.services.analytics.sync import (
    PublicationNotUploadedError as MetricsPublicationNotUploadedError,
)
from app.services.analytics.sync import sync_video_metrics
from app.services.comments.sync import (
    PublicationNotFoundError as CommentsPublicationNotFoundError,
)
from app.services.comments.sync import (
    PublicationNotUploadedError as CommentsPublicationNotUploadedError,
)
from app.services.comments.sync import sync_comments
from app.services.feedback.insights import (
    PublicationNotFoundError as InsightPublicationNotFoundError,
)
from app.services.feedback.insights import generate_publication_insights
from app.services.feedback.sync import (
    sync_all_completed_publications_feedback,
    sync_publication_feedback,
)

router = APIRouter(tags=["analytics"])

DbSession = Annotated[Session, Depends(get_db)]


def _ensure_publication(db: Session, publication_id: str) -> Publication:
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise HTTPException(status_code=404, detail=f"Publication not found: {publication_id}")
    return publication


def _provider_error(exc: Exception) -> HTTPException:
    if isinstance(exc, QuotaExceededError):
        return HTTPException(status_code=429, detail=str(exc))
    if isinstance(exc, AuthError):
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, TransientAPIError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


@router.get(
    "/publications/{publication_id}/metrics",
    response_model=list[VideoMetricDailyResponse],
)
def list_metrics_endpoint(publication_id: str, db: DbSession) -> list[VideoMetricDailyResponse]:
    _ensure_publication(db, publication_id)
    metrics = (
        db.query(VideoMetricDaily)
        .filter(VideoMetricDaily.publication_id == publication_id)
        .order_by(VideoMetricDaily.metric_date.desc())
        .all()
    )
    return [VideoMetricDailyResponse.model_validate(metric) for metric in metrics]


@router.post(
    "/publications/{publication_id}/sync-metrics",
    response_model=VideoMetricDailyResponse,
)
def sync_metrics_endpoint(publication_id: str, db: DbSession) -> VideoMetricDailyResponse:
    provider = get_youtube_provider()
    try:
        metric = asyncio.run(
            sync_video_metrics(db, publication_id=publication_id, provider=provider)
        )
    except MetricsPublicationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MetricsPublicationNotUploadedError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (QuotaExceededError, AuthError, TransientAPIError) as exc:
        db.rollback()
        raise _provider_error(exc) from exc
    db.commit()
    db.refresh(metric)
    return VideoMetricDailyResponse.model_validate(metric)


@router.get(
    "/publications/{publication_id}/comments",
    response_model=list[CommentResponse],
)
def list_comments_endpoint(
    publication_id: str,
    db: DbSession,
    category: str | None = Query(default=None),
    include_deleted: bool = Query(default=False),
) -> list[CommentResponse]:
    _ensure_publication(db, publication_id)
    if category is not None and category not in COMMENT_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Unknown comment category: {category}")

    query = db.query(Comment).filter(Comment.publication_id == publication_id)
    if category is not None:
        query = query.filter(Comment.category == category)
    if not include_deleted:
        query = query.filter(Comment.moderation_status != "deleted")
    comments = query.order_by(Comment.priority.desc(), Comment.published_at.desc()).all()
    return [CommentResponse.model_validate(comment) for comment in comments]


@router.post(
    "/publications/{publication_id}/sync-comments",
    response_model=list[CommentResponse],
)
def sync_comments_endpoint(publication_id: str, db: DbSession) -> list[CommentResponse]:
    provider = get_youtube_provider()
    try:
        comments = asyncio.run(sync_comments(db, publication_id=publication_id, provider=provider))
    except CommentsPublicationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CommentsPublicationNotUploadedError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (QuotaExceededError, AuthError, TransientAPIError) as exc:
        db.rollback()
        raise _provider_error(exc) from exc
    db.commit()
    return [CommentResponse.model_validate(comment) for comment in comments]


@router.get(
    "/publications/{publication_id}/insights",
    response_model=list[InsightResponse],
)
def list_insights_endpoint(publication_id: str, db: DbSession) -> list[InsightResponse]:
    _ensure_publication(db, publication_id)
    insights = (
        db.query(Insight)
        .filter(Insight.source_type == "publication", Insight.source_id == publication_id)
        .order_by(Insight.created_at.desc())
        .all()
    )
    return [InsightResponse.model_validate(insight) for insight in insights]


@router.post(
    "/publications/{publication_id}/generate-insights",
    response_model=list[InsightResponse],
)
def generate_insights_endpoint(publication_id: str, db: DbSession) -> list[InsightResponse]:
    try:
        insights = generate_publication_insights(db, publication_id=publication_id)
    except InsightPublicationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return [InsightResponse.model_validate(insight) for insight in insights]


@router.post(
    "/publications/{publication_id}/sync-feedback",
    response_model=FeedbackSyncResponse,
)
def sync_feedback_endpoint(publication_id: str, db: DbSession) -> FeedbackSyncResponse:
    provider = get_youtube_provider()
    try:
        result = asyncio.run(
            sync_publication_feedback(db, publication_id=publication_id, provider=provider)
        )
    except (
        MetricsPublicationNotFoundError,
        CommentsPublicationNotFoundError,
        InsightPublicationNotFoundError,
    ) as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (MetricsPublicationNotUploadedError, CommentsPublicationNotUploadedError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (QuotaExceededError, AuthError, TransientAPIError) as exc:
        db.rollback()
        raise _provider_error(exc) from exc
    db.commit()
    db.refresh(result.metric)
    return FeedbackSyncResponse(
        metric=VideoMetricDailyResponse.model_validate(result.metric),
        comments_synced=len(result.comments),
        insights=[InsightResponse.model_validate(insight) for insight in result.insights],
    )


@router.post("/analytics/sync-completed", response_model=list[FeedbackSyncResponse])
def sync_completed_feedback_endpoint(db: DbSession) -> list[FeedbackSyncResponse]:
    provider = get_youtube_provider()
    try:
        results = asyncio.run(sync_all_completed_publications_feedback(db, provider=provider))
    except (QuotaExceededError, AuthError, TransientAPIError) as exc:
        db.rollback()
        raise _provider_error(exc) from exc
    db.commit()
    return [
        FeedbackSyncResponse(
            metric=VideoMetricDailyResponse.model_validate(result.metric),
            comments_synced=len(result.comments),
            insights=[InsightResponse.model_validate(insight) for insight in result.insights],
        )
        for result in results
    ]
