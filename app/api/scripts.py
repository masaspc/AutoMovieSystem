"""台本生成・取得用JSON API。"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.script import Script
from app.providers.llm.factory import get_llm_provider
from app.schemas.scripts import FindingResponse, ScriptResponse
from app.services.jobs import JobInProgressError
from app.services.scripts.generator import TopicNotFoundError, generate_script
from app.services.scripts.inspector import inspect_script_with_history

router = APIRouter(tags=["scripts"])

DbSession = Annotated[Session, Depends(get_db)]


def _to_response(session: Session, script: Script) -> ScriptResponse:
    findings = inspect_script_with_history(session, script)
    response = ScriptResponse.model_validate(script)
    response.findings = [
        FindingResponse(code=f.code, severity=f.severity, message=f.message) for f in findings
    ]
    return response


@router.post("/topics/{topic_id}/generate-script", response_model=ScriptResponse, status_code=201)
def generate_script_endpoint(topic_id: str, db: DbSession) -> ScriptResponse:
    provider = get_llm_provider()
    try:
        script = asyncio.run(generate_script(db, topic_id=topic_id, provider=provider))
    except TopicNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobInProgressError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(script)
    return _to_response(db, script)


@router.get("/scripts/{script_id}", response_model=ScriptResponse)
def get_script(script_id: str, db: DbSession) -> ScriptResponse:
    script = db.get(Script, script_id)
    if script is None:
        raise HTTPException(status_code=404, detail=f"Script not found: {script_id}")
    return _to_response(db, script)
