"""Topic用JSON API(手動作成・CSVインポート・一覧・スコアリング)。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.topic import Topic
from app.schemas.topics import (
    TopicCreateRequest,
    TopicImportCsvRequest,
    TopicImportCsvResponse,
    TopicResponse,
)
from app.services.topics.importer import InvalidCsvError, create_manual_topic, import_topics_csv
from app.services.topics.scoring import InvalidWeightsError, TopicNotFoundError, score_topic

router = APIRouter(prefix="/topics", tags=["topics"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post("", response_model=TopicResponse, status_code=201)
def create_topic(payload: TopicCreateRequest, db: DbSession) -> Topic:
    topic = create_manual_topic(
        db,
        channel_id=payload.channel_id,
        title=payload.title,
        description=payload.description,
        client_key=payload.client_key,
    )
    db.commit()
    db.refresh(topic)
    return topic


@router.post("/import-csv", response_model=TopicImportCsvResponse)
def import_csv(payload: TopicImportCsvRequest, db: DbSession) -> TopicImportCsvResponse:
    try:
        result = import_topics_csv(db, channel_id=payload.channel_id, csv_text=payload.csv_text)
    except InvalidCsvError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return TopicImportCsvResponse(
        created=result.created, skipped=result.skipped, total_rows=result.total_rows
    )


@router.get("", response_model=list[TopicResponse])
def list_topics(db: DbSession, channel_id: str | None = None) -> list[Topic]:
    query = db.query(Topic)
    if channel_id is not None:
        query = query.filter(Topic.channel_id == channel_id)
    return query.order_by(Topic.created_at.desc()).all()


@router.post("/{topic_id}/score", response_model=TopicResponse)
def score_topic_endpoint(topic_id: str, db: DbSession) -> Topic:
    try:
        topic = score_topic(db, topic_id)
    except TopicNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidWeightsError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    db.commit()
    db.refresh(topic)
    return topic
