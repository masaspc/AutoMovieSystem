"""公開可否ゲート(仕様§12・docs/architecture.md 公開ゲート節)。fail-closed。

1つでも条件を欠けば `(False, reasons)` を返す。判定不能な場合も公開可としない。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.approval import Approval
from app.models.review import Review
from app.models.script import Script
from app.models.video_project import VideoProject
from app.services.media.renderer import compute_file_checksum


def _latest_review(session: Session, video_project_id: str, reviewer_type: str) -> Review | None:
    max_version = (
        session.query(func.max(Review.review_version))
        .filter(Review.video_project_id == video_project_id, Review.reviewer_type == reviewer_type)
        .scalar()
    )
    if max_version is None:
        return None
    return (
        session.query(Review)
        .filter(
            Review.video_project_id == video_project_id,
            Review.reviewer_type == reviewer_type,
            Review.review_version == max_version,
        )
        .one_or_none()
    )


def can_auto_publish(session: Session, video_project_id: str) -> tuple[bool, list[str]]:
    """自動公開可否をfail-closedで判定する。`(可否, 理由リスト)` を返す。"""
    settings = get_settings()

    if not settings.AUTO_PUBLISH_ENABLED:
        return False, ["auto publish disabled"]

    reasons: list[str] = []

    project = session.get(VideoProject, video_project_id)
    if project is None:
        return False, ["video project not found"]

    machine_review = _latest_review(session, video_project_id, "machine")
    content_review = _latest_review(session, video_project_id, "content")

    if machine_review is None or not machine_review.passed:
        reasons.append("machine review not passed or missing")
    if content_review is None or not content_review.passed:
        reasons.append("content review not passed or missing")

    for review in (machine_review, content_review):
        if review is not None and review.blocking_findings:
            reasons.append(f"blocking findings present ({review.reviewer_type})")

    if settings.REQUIRE_HUMAN_APPROVAL:
        approved = (
            session.query(Approval)
            .filter(Approval.video_project_id == video_project_id, Approval.decision == "approved")
            .first()
        )
        if approved is None:
            reasons.append("human approval required but missing")

    if not project.checksum or not project.output_path:
        reasons.append("checksum or output path missing")
    else:
        output_path = Path(project.output_path)
        if not output_path.exists():
            reasons.append("output file missing")
        elif compute_file_checksum(output_path) != project.checksum:
            reasons.append("checksum mismatch")

    if project.script_id:
        script = session.get(Script, project.script_id)
        if script is not None:
            text_blob = " ".join(
                [
                    script.title or "",
                    (script.body or {}).get("description", "") or "",
                    script.hook or "",
                ]
            )
            if any(keyword in text_blob for keyword in settings.resolved_high_risk_keywords):
                reasons.append("high risk topic keyword detected")

    return (len(reasons) == 0, reasons)
