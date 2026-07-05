"""承認フロー(仕様§12・§17)。`AUTOMATED_REVIEW_PASSED` からの人間承認/却下。

重要操作のため `decided_by` を含む監査ログを構造化ログへ記録する(セキュリティ要点参照)。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.approval import Approval
from app.models.video_project import VideoProject
from app.services.state_machine import transition

logger = get_logger(__name__)


class VideoProjectNotFoundError(ValueError):
    """指定されたVideoProjectが存在しない場合。"""


def _get_video_project(session: Session, video_project_id: str) -> VideoProject:
    project = session.get(VideoProject, video_project_id)
    if project is None:
        raise VideoProjectNotFoundError(f"VideoProject not found: {video_project_id}")
    return project


def approve(
    session: Session, *, video_project_id: str, decided_by: str, reason: str | None = None
) -> VideoProject:
    """`AUTOMATED_REVIEW_PASSED` -> `HUMAN_APPROVED` -> `UPLOAD_READY`。

    状態が `AUTOMATED_REVIEW_PASSED` でない場合は `InvalidTransitionError` を送出し、
    Approvalレコードは作成しない(遷移が先、記録が後)。
    """
    project = _get_video_project(session, video_project_id)
    transition(project, "HUMAN_APPROVED")
    transition(project, "UPLOAD_READY")

    approval = Approval(
        video_project_id=video_project_id,
        decision="approved",
        decided_by=decided_by,
        reason=reason,
    )
    session.add(approval)
    session.flush()

    logger.info("video_project_approved", video_project_id=video_project_id, decided_by=decided_by)
    return project


def reject(
    session: Session, *, video_project_id: str, decided_by: str, reason: str | None = None
) -> VideoProject:
    """`AUTOMATED_REVIEW_PASSED` -> `REJECTED`(終端)。"""
    project = _get_video_project(session, video_project_id)
    transition(project, "REJECTED")

    approval = Approval(
        video_project_id=video_project_id,
        decision="rejected",
        decided_by=decided_by,
        reason=reason,
    )
    session.add(approval)
    session.flush()

    logger.info("video_project_rejected", video_project_id=video_project_id, decided_by=decided_by)
    return project
