"""ジョブ冪等性基盤(docs/architecture.md 冪等性設計 / D-016 / D-017)。

Celeryタスク・APIハンドラの双方から同期SQLAlchemyセッション経由で使う薄いヘルパー。
`JobRun.idempotency_key` の UNIQUE 制約を用いて以下を保証する:

- 既に succeeded な JobRun があれば実行せず skip する
- started のまま残った JobRun は「lease(貸与期間)」が有効な間は他プロセスが実行中と
  みなし、fn を呼ばず in_progress を返す(D-016: 並行再実行防止)。lease超過(クラッシュ
  残骸=stale)または failed な JobRun は attempt をインクリメントして再実行する
- 新規行INSERT時に並行実行でUNIQUE違反になった場合は「他が実行済み」として skip する
- 実行関数(fn)が例外を送出した場合は failed として記録し、例外を再送出する
  (D-017: この記録は呼び出し元セッションのflushのみであり、呼び出し側が後で
  rollbackすると消える。Celeryタスクラッパーは `record_failure_in_new_session` を
  使って新規セッションで再永続化すること)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger, mask_secrets_in_text
from app.models.job_run import JobRun

logger = get_logger(__name__)

# last_error に保存するメッセージの最大長(ログ肥大・意図しない大量データ保存を防ぐ)。
_MAX_ERROR_LENGTH = 2000


class JobInProgressError(RuntimeError):
    """既に(lease有効期間内で)実行中の JobRun に対して再実行が要求された場合。"""


@dataclass
class JobResult[T]:
    """`run_idempotent` の戻り値。"""

    status: str  # "succeeded" | "skipped" | "in_progress"
    job_run: JobRun
    result: T | None = None


def _lease_active(job_run: JobRun, lease_timeout_seconds: float) -> bool:
    lease_deadline = job_run.started_at + timedelta(seconds=lease_timeout_seconds)
    return lease_deadline > datetime.utcnow()


def _resolve_lease_timeout(lease_timeout_seconds: float | None) -> float:
    if lease_timeout_seconds is not None:
        return lease_timeout_seconds
    return float(get_settings().JOB_LEASE_TIMEOUT_SECONDS)


def run_idempotent[T](
    session: Session,
    *,
    job_type: str,
    entity_type: str,
    entity_id: str,
    idempotency_key: str,
    fn: Callable[[], T],
    trace_id: str | None = None,
    lease_timeout_seconds: float | None = None,
) -> JobResult[T]:
    """`idempotency_key` を用いて `fn` を冪等に実行する。

    Args:
        session: 呼び出し元が管理するSQLAlchemyセッション(コミットは呼び出し側の責務)。
        job_type: ジョブ種別(例: "score_topic")。
        entity_type: 対象エンティティ種別(例: "topic")。
        entity_id: 対象エンティティID。
        idempotency_key: 冪等キー(例: "score_topic:{topic_id}")。
        fn: 実際の処理。引数なしで呼び出される。
        trace_id: 分散トレーシング用ID(任意)。
        lease_timeout_seconds: lease期間(秒)。未指定なら設定値
            `JOB_LEASE_TIMEOUT_SECONDS` を使う。

    Returns:
        JobResult。status="skipped"/"in_progress" の場合 fn は呼ばれていない。
    """
    lease_timeout = _resolve_lease_timeout(lease_timeout_seconds)
    existing = session.query(JobRun).filter(JobRun.idempotency_key == idempotency_key).one_or_none()

    if existing is not None and existing.status == "succeeded":
        logger.info(
            "job_run_skipped_already_succeeded",
            job_type=job_type,
            idempotency_key=idempotency_key,
        )
        return JobResult(status="skipped", job_run=existing)

    if existing is not None and existing.status == "started":
        if _lease_active(existing, lease_timeout):
            logger.info(
                "job_run_in_progress_lease_active",
                job_type=job_type,
                idempotency_key=idempotency_key,
            )
            return JobResult(status="in_progress", job_run=existing)
        logger.warning(
            "job_run_stale_lease_expired_retrying",
            job_type=job_type,
            idempotency_key=idempotency_key,
        )

    job_run: JobRun
    if existing is not None:
        # lease超過(クラッシュ残骸=stale) か failed -> attempt を増やして再実行。
        job_run = existing
        job_run.attempt += 1
        job_run.status = "started"
        job_run.started_at = datetime.utcnow()
        job_run.finished_at = None
        job_run.last_error = None
        job_run.trace_id = trace_id
        session.flush()
    else:
        job_run = JobRun(
            job_type=job_type,
            entity_type=entity_type,
            entity_id=entity_id,
            idempotency_key=idempotency_key,
            status="started",
            attempt=1,
            trace_id=trace_id,
        )
        try:
            with session.begin_nested():
                session.add(job_run)
                session.flush()
        except IntegrityError:
            # 並行実行で他プロセスが先にINSERTした -> 自分は実行せずskip。
            session.expunge(job_run)
            winner = (
                session.query(JobRun)
                .filter(JobRun.idempotency_key == idempotency_key)
                .one_or_none()
            )
            logger.info(
                "job_run_skipped_concurrent_insert",
                job_type=job_type,
                idempotency_key=idempotency_key,
            )
            if winner is None:  # pragma: no cover - 理論上到達しない防御的分岐
                raise
            return JobResult(status="skipped", job_run=winner)

    try:
        result = fn()
    except Exception as exc:
        job_run.status = "failed"
        job_run.finished_at = datetime.utcnow()
        # シークレットを含めないよう、マスキングした例外メッセージのみを切り詰めて保存する。
        job_run.last_error = mask_secrets_in_text(str(exc))[:_MAX_ERROR_LENGTH]
        session.flush()
        # D-017: 呼び出し元がこの後 rollback すると上記の記録は消える。Celeryタスク
        # ラッパーが新規セッションで再永続化できるよう、idempotency_keyを例外に付与する。
        exc.idempotency_key = idempotency_key  # type: ignore[attr-defined]
        logger.error(
            "job_run_failed",
            job_type=job_type,
            idempotency_key=idempotency_key,
            error_type=type(exc).__name__,
        )
        raise

    job_run.status = "succeeded"
    job_run.finished_at = datetime.utcnow()
    session.flush()
    logger.info("job_run_succeeded", job_type=job_type, idempotency_key=idempotency_key)
    return JobResult(status="succeeded", job_run=job_run, result=result)


async def run_idempotent_async[T](
    session: Session,
    *,
    job_type: str,
    entity_type: str,
    entity_id: str,
    idempotency_key: str,
    fn: Callable[[JobRun], Awaitable[T]],
    trace_id: str | None = None,
    lease_timeout_seconds: float | None = None,
) -> JobResult[T]:
    """`run_idempotent` の非同期版。

    LLM/TTSなど async Protocol (D-013) を呼び出すサービス層向け。`fn` は現在の
    `JobRun` を受け取る(UsageRecord記録などで `job_run_id` を使うため)。
    冪等性の判定・JobRun管理ロジックは同期版と同一(D-016のleaseも同様)。
    """
    lease_timeout = _resolve_lease_timeout(lease_timeout_seconds)
    existing = session.query(JobRun).filter(JobRun.idempotency_key == idempotency_key).one_or_none()

    if existing is not None and existing.status == "succeeded":
        logger.info(
            "job_run_skipped_already_succeeded",
            job_type=job_type,
            idempotency_key=idempotency_key,
        )
        return JobResult(status="skipped", job_run=existing)

    if existing is not None and existing.status == "started":
        if _lease_active(existing, lease_timeout):
            logger.info(
                "job_run_in_progress_lease_active",
                job_type=job_type,
                idempotency_key=idempotency_key,
            )
            return JobResult(status="in_progress", job_run=existing)
        logger.warning(
            "job_run_stale_lease_expired_retrying",
            job_type=job_type,
            idempotency_key=idempotency_key,
        )

    job_run: JobRun
    if existing is not None:
        job_run = existing
        job_run.attempt += 1
        job_run.status = "started"
        job_run.started_at = datetime.utcnow()
        job_run.finished_at = None
        job_run.last_error = None
        job_run.trace_id = trace_id
        session.flush()
    else:
        job_run = JobRun(
            job_type=job_type,
            entity_type=entity_type,
            entity_id=entity_id,
            idempotency_key=idempotency_key,
            status="started",
            attempt=1,
            trace_id=trace_id,
        )
        try:
            with session.begin_nested():
                session.add(job_run)
                session.flush()
        except IntegrityError:
            session.expunge(job_run)
            winner = (
                session.query(JobRun)
                .filter(JobRun.idempotency_key == idempotency_key)
                .one_or_none()
            )
            logger.info(
                "job_run_skipped_concurrent_insert",
                job_type=job_type,
                idempotency_key=idempotency_key,
            )
            if winner is None:  # pragma: no cover - 理論上到達しない防御的分岐
                raise
            return JobResult(status="skipped", job_run=winner)

    try:
        result = await fn(job_run)
    except Exception as exc:
        job_run.status = "failed"
        job_run.finished_at = datetime.utcnow()
        job_run.last_error = mask_secrets_in_text(str(exc))[:_MAX_ERROR_LENGTH]
        session.flush()
        exc.idempotency_key = idempotency_key  # type: ignore[attr-defined]
        logger.error(
            "job_run_failed",
            job_type=job_type,
            idempotency_key=idempotency_key,
            error_type=type(exc).__name__,
        )
        raise

    job_run.status = "succeeded"
    job_run.finished_at = datetime.utcnow()
    session.flush()
    logger.info("job_run_succeeded", job_type=job_type, idempotency_key=idempotency_key)
    return JobResult(status="succeeded", job_run=job_run, result=result)


def record_failure_in_new_session(
    *,
    idempotency_key: str,
    job_type: str,
    entity_type: str,
    entity_id: str,
    error: BaseException,
    trace_id: str | None = None,
) -> None:
    """D-017: Celeryタスクラッパーの except 節専用のヘルパー。

    呼び出し元セッションで `session.rollback()` した後でも JobRun の失敗記録が
    残るよう、**新規セッション・新規トランザクション**で JobRun を取得(なければ作成)し
    `failed` + `last_error` + `finished_at` を記録して commit する。

    サービス層(`run_idempotent`/`run_idempotent_async`)のトランザクション契約は
    変更しない。このヘルパーはタスクラッパー層からのみ呼び出すこと。
    """
    from app.db.session import SessionLocal  # 遅延import(テストでの差し替えを可能にする)

    new_session = SessionLocal()
    try:
        job_run = (
            new_session.query(JobRun)
            .filter(JobRun.idempotency_key == idempotency_key)
            .one_or_none()
        )
        if job_run is None:
            job_run = JobRun(
                job_type=job_type,
                entity_type=entity_type,
                entity_id=entity_id,
                idempotency_key=idempotency_key,
                status="failed",
                attempt=1,
            )
            new_session.add(job_run)
        else:
            job_run.status = "failed"

        job_run.finished_at = datetime.utcnow()
        job_run.last_error = mask_secrets_in_text(str(error))[:_MAX_ERROR_LENGTH]
        if trace_id is not None:
            job_run.trace_id = trace_id
        new_session.commit()
        logger.info(
            "job_run_failure_persisted_in_new_session",
            job_type=job_type,
            idempotency_key=idempotency_key,
        )
    except Exception:
        new_session.rollback()
        logger.error(
            "record_failure_in_new_session_failed",
            job_type=job_type,
            idempotency_key=idempotency_key,
        )
        raise
    finally:
        new_session.close()
