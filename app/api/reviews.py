"""Review取得用JSON API。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.review import Review
from app.schemas.reviews import ReviewResponse

router = APIRouter(tags=["reviews"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/video-projects/{video_project_id}/reviews", response_model=list[ReviewResponse])
def list_reviews(video_project_id: str, db: DbSession) -> list[Review]:
    return (
        db.query(Review)
        .filter(Review.video_project_id == video_project_id)
        .order_by(Review.reviewer_type, Review.review_version)
        .all()
    )
