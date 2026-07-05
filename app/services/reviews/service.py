"""自動レビュージョブ(仕様§12)。machine+content実行 -> Review保存 -> 状態遷移。

`VIDEO_RENDERED` -> 両方合格なら `AUTOMATED_REVIEW_PASSED`、いずれか不合格なら `REVIEW_FAILED`。
冪等キーは checksum ベース(`review:{video_project_id}:{checksum}`)。同一checksumでの
再実行は `JobRun` により skip され、Reviewは増えない。
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.job_run import JobRun
from app.models.review import Review
from app.models.script import Script
from app.models.video_project import VideoProject
from app.providers.llm.base import LLMProvider
from app.services.jobs import run_idempotent_async
from app.services.reviews import machine
from app.services.reviews.content import inspect_content
from app.services.reviews.findings import Finding, to_dict
from app.services.state_machine import transition

logger = get_logger(__name__)


class VideoProjectNotFoundError(ValueError):
    """指定されたVideoProjectが存在しない場合。"""


class ScriptNotFoundError(ValueError):
    """VideoProjectに紐づくScriptが存在しない、または未設定の場合。"""


class MissingChecksumError(ValueError):
    """レンダリング未完了(checksum未設定)でレビューを実行しようとした場合。"""


def build_review_idempotency_key(video_project_id: str, checksum: str) -> str:
    return f"review:{video_project_id}:{checksum}"


def _get_video_project(session: Session, video_project_id: str) -> VideoProject:
    project = session.get(VideoProject, video_project_id)
    if project is None:
        raise VideoProjectNotFoundError(f"VideoProject not found: {video_project_id}")
    return project


def _get_script(session: Session, project: VideoProject) -> Script:
    if project.script_id is None:
        raise ScriptNotFoundError(f"VideoProject({project.id})にScriptが紐づいていません")
    script = session.get(Script, project.script_id)
    if script is None:
        raise ScriptNotFoundError(f"Script not found: {project.script_id}")
    return script


def _next_review_version(session: Session, video_project_id: str) -> int:
    max_version = (
        session.query(func.max(Review.review_version))
        .filter(Review.video_project_id == video_project_id)
        .scalar()
    )
    return 1 if max_version is None else max_version + 1


def _score_from_findings(findings: list[Finding]) -> float:
    blocking = sum(1 for f in findings if f.severity == "blocking")
    warning = sum(1 for f in findings if f.severity == "warning")
    return max(0.0, min(100.0, 100.0 - (blocking * 25.0) - (warning * 5.0)))


def _save_review(
    session: Session,
    *,
    video_project_id: str,
    reviewer_type: str,
    review_version: int,
    findings: list[Finding],
) -> Review:
    blocking = [f for f in findings if f.severity == "blocking"]
    review = Review(
        video_project_id=video_project_id,
        reviewer_type=reviewer_type,
        review_version=review_version,
        score=_score_from_findings(findings),
        passed=len(blocking) == 0,
        findings=[to_dict(f) for f in findings],
        blocking_findings=[to_dict(f) for f in blocking],
    )
    session.add(review)
    session.flush()
    return review


async def run_automated_review(
    session: Session, *, video_project_id: str, provider: LLMProvider
) -> VideoProject:
    """machine+contentレビューを実行しReviewを保存、状態遷移する(冪等)。

    予算超過(`BudgetExceededError`)はレビュー不合格ではなく保留として例外を伝播する
    (`JobRun` が failed として記録され、`VideoProject.status` は変更されない)。
    """
    project = _get_video_project(session, video_project_id)
    script = _get_script(session, project)
    if not project.checksum:
        raise MissingChecksumError(
            f"VideoProject({video_project_id})にchecksumが未設定です(レンダリング未完了)"
        )

    idempotency_key = build_review_idempotency_key(video_project_id, project.checksum)

    async def _do_review(job_run: JobRun) -> VideoProject:
        review_version = _next_review_version(session, video_project_id)

        machine_findings = machine.inspect_machine(session, project)
        content_findings = await inspect_content(
            session, project, script, provider=provider, job_run_id=job_run.id
        )

        machine_review = _save_review(
            session,
            video_project_id=video_project_id,
            reviewer_type="machine",
            review_version=review_version,
            findings=machine_findings,
        )
        content_review = _save_review(
            session,
            video_project_id=video_project_id,
            reviewer_type="content",
            review_version=review_version,
            findings=content_findings,
        )

        if machine_review.passed and content_review.passed:
            transition(project, "AUTOMATED_REVIEW_PASSED")
        else:
            transition(project, "REVIEW_FAILED")
        session.flush()
        return project

    job_result = await run_idempotent_async(
        session,
        job_type="automated_review",
        entity_type="video_project",
        entity_id=video_project_id,
        idempotency_key=idempotency_key,
        fn=_do_review,
    )
    if job_result.status == "skipped":
        return project
    assert job_result.result is not None
    return job_result.result


def restart_review(session: Session, *, video_project_id: str) -> VideoProject:
    """再レビュー: `REVIEW_FAILED` -> `VIDEO_RENDERED`(復旧エッジ)。"""
    project = _get_video_project(session, video_project_id)
    transition(project, "VIDEO_RENDERED")
    session.flush()
    return project
