"""公開予約サービス(仕様§12・docs/architecture.md 公開ゲート)。

`app.services.reviews.gate.can_auto_publish`(自動レビュー合格/人間承認/チェックサム一致/
高リスクキーワード/AUTO_PUBLISH_ENABLED)に加え、本モジュールで残る2条件
(重複 youtube_video_id なし・有効なOAuth認証)を検証し、公開ゲート6条件の
すべてを満たす場合のみ `provider.set_schedule` を実行して `SCHEDULED` へ遷移する。
1つでも満たさなければ例外ではなく理由付きの `ScheduleResult`(fail-closed)を返す。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.publication import Publication
from app.models.video_project import VideoProject
from app.providers.youtube.base import YouTubeProvider
from app.services.reviews.gate import can_auto_publish
from app.services.state_machine import InvalidTransitionError, transition

logger = get_logger(__name__)


class PublicationNotFoundError(ValueError):
    """指定されたPublicationが存在しない場合。"""


@dataclass(frozen=True)
class ScheduleResult:
    """`schedule_publication` の戻り値。ゲート拒否時も例外にせず理由を返す(fail-closed)。"""

    scheduled: bool
    reasons: list[str] = field(default_factory=list)
    publication: Publication | None = None


def _get_publication(session: Session, publication_id: str) -> Publication:
    publication = session.get(Publication, publication_id)
    if publication is None:
        raise PublicationNotFoundError(f"Publication not found: {publication_id}")
    return publication


async def _check_full_publish_gate(
    session: Session, project: VideoProject, publication: Publication, provider: YouTubeProvider
) -> list[str]:
    """公開ゲート6条件のうち、`can_auto_publish` が扱わない残り2条件を含めて検証する。"""
    allowed, reasons = can_auto_publish(session, project.id)
    reasons = list(reasons)
    if allowed:
        reasons = []

    # 条件: 重複 youtube_video_id なし(このVideoProjectに既に別の完了済みPublicationがないか)。
    other_completed = (
        session.query(Publication)
        .filter(
            Publication.video_project_id == project.id,
            Publication.upload_status == "completed",
            Publication.id != publication.id,
        )
        .count()
    )
    if other_completed > 0:
        reasons.append("duplicate completed publication exists for this video project")

    # 条件: 有効なOAuth認証。
    auth_ok = await provider.check_auth()
    if not auth_ok:
        reasons.append("youtube oauth authentication invalid")

    return reasons


async def schedule_publication(
    session: Session, *, publication_id: str, publish_at: datetime, provider: YouTubeProvider
) -> ScheduleResult:
    """`UPLOADED_PRIVATE` -> `SCHEDULED`。ゲート条件を1つでも欠けば拒否結果を返す。"""
    publication = _get_publication(session, publication_id)

    project = session.get(VideoProject, publication.video_project_id)
    if project is None:
        raise PublicationNotFoundError(f"VideoProject not found for publication: {publication_id}")

    reasons: list[str] = []
    if publication.upload_status != "completed" or not publication.youtube_video_id:
        reasons.append("publication is not completed (upload not finished)")

    reasons.extend(await _check_full_publish_gate(session, project, publication, provider))

    if reasons:
        logger.info("schedule_publication_rejected", publication_id=publication_id, reasons=reasons)
        return ScheduleResult(scheduled=False, reasons=reasons, publication=publication)

    try:
        transition(project, "SCHEDULED")
    except InvalidTransitionError as exc:
        reasons.append(f"invalid state transition: {exc}")
        logger.info("schedule_publication_rejected", publication_id=publication_id, reasons=reasons)
        return ScheduleResult(scheduled=False, reasons=reasons, publication=publication)

    assert publication.youtube_video_id is not None
    await provider.set_schedule(
        youtube_video_id=publication.youtube_video_id, publish_at=publish_at
    )

    publication.scheduled_at = publish_at
    session.flush()

    logger.info("schedule_publication_succeeded", publication_id=publication_id)
    return ScheduleResult(scheduled=True, reasons=[], publication=publication)


def finalize_due_publications(session: Session, *, now: datetime | None = None) -> int:
    """公開期日到来のPublicationを確定させる(修正5)。

    `scheduled_at <= now(UTC)` かつ `published_at IS NULL` かつ
    `upload_status == "completed"` のPublicationについて `published_at` を設定し、
    対応する VideoProject が `SCHEDULED` であれば状態遷移表経由で
    `PUBLISHED` -> `METRICS_COLLECTING` へ進める。`published_at IS NULL` を条件に
    含めるため、同じPublicationを再度確定させることはない(二重更新なし)。

    戻り値は確定件数。
    """
    current_time = now or datetime.now(UTC)
    # SQLite/PostgreSQL双方の DateTime(naive) 列と比較するためnaive化する(UTC前提)。
    naive_now = (
        current_time.replace(tzinfo=None) if current_time.tzinfo is not None else current_time
    )

    due_publications = (
        session.query(Publication)
        .filter(
            Publication.scheduled_at.isnot(None),
            Publication.scheduled_at <= naive_now,
            Publication.published_at.is_(None),
            Publication.upload_status == "completed",
        )
        .all()
    )

    finalized_count = 0
    for publication in due_publications:
        publication.published_at = naive_now

        project = session.get(VideoProject, publication.video_project_id)
        if project is not None and project.status == "SCHEDULED":
            try:
                transition(project, "PUBLISHED")
                transition(project, "METRICS_COLLECTING")
            except InvalidTransitionError as exc:
                logger.info(
                    "finalize_due_publication_transition_skipped",
                    publication_id=publication.id,
                    video_project_id=project.id,
                    current_state=project.status,
                    error=str(exc),
                )

        finalized_count += 1
        logger.info("finalize_due_publication_succeeded", publication_id=publication.id)

    session.flush()
    return finalized_count
