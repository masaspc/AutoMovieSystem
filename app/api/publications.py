"""アップロード・公開予約用JSON API。"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.providers.youtube.base import (
    AuthError,
    QuotaExceededError,
    TransientAPIError,
)
from app.providers.youtube.factory import get_youtube_provider
from app.schemas.publications import PublicationResponse, ScheduleRequest, ScheduleResponse
from app.services.jobs import JobInProgressError
from app.services.publishing.scheduler import PublicationNotFoundError, schedule_publication
from app.services.publishing.uploader import (
    ScriptNotFoundError,
    UploadPreconditionError,
    VideoProjectNotFoundError,
    upload_video,
)

router = APIRouter(tags=["publications"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post(
    "/video-projects/{video_project_id}/upload",
    response_model=PublicationResponse,
    status_code=201,
)
def upload_video_endpoint(video_project_id: str, db: DbSession) -> PublicationResponse:
    provider = get_youtube_provider()
    try:
        publication = asyncio.run(
            upload_video(db, video_project_id=video_project_id, provider=provider)
        )
    except (VideoProjectNotFoundError, ScriptNotFoundError) as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UploadPreconditionError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except JobInProgressError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except QuotaExceededError as exc:
        db.commit()
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except AuthError as exc:
        db.commit()
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except TransientAPIError as exc:
        db.commit()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    db.commit()
    db.refresh(publication)
    return PublicationResponse.model_validate(publication)


@router.post(
    "/publications/{publication_id}/schedule",
    response_model=ScheduleResponse,
)
def schedule_publication_endpoint(
    publication_id: str, payload: ScheduleRequest, db: DbSession
) -> ScheduleResponse:
    provider = get_youtube_provider()
    try:
        result = asyncio.run(
            schedule_publication(
                db,
                publication_id=publication_id,
                publish_at=payload.publish_at,
                provider=provider,
            )
        )
    except PublicationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    publication_response = (
        PublicationResponse.model_validate(result.publication)
        if result.publication is not None
        else None
    )
    return ScheduleResponse(
        scheduled=result.scheduled, reasons=result.reasons, publication=publication_response
    )
