"""ジョブ冪等性基盤(docs/architecture.md 冪等性設計)。

Celeryタスク・APIハンドラの双方から同期SQLAlchemyセッション経由で使う薄いヘルパー。
`JobRun.idempotency_key` の UNIQUE 制約を用いて以下を保証する:

- 既に succeeded な JobRun があれば実行せず skip する
- started のまま残った JobRun(クラッシュ後の残骸)や failed な JobRun は
  attempt をインクリメントして再実行する
- 新規行INSERT時に並行実行でUNIQUE違反になった場合は「他が実行済み」として skip する
- 実行関数(fn)が例外を送出した場合は failed として記録し、例外を再送出する
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.job_run import JobRun

logger = get_logger(__name__)

# last_error に保存するメッセージの最大長(ログ肥大・意図しない大量データ保存を防ぐ)。
_MAX_ERROR_LENGTH = 2000


@dataclass
class JobResult[T]:
    """`run_idempotent` の戻り値。"""

    status: str  # "succeeded" | "skipped"
    job_run: JobRun
    result: T | None = None


def run_idempotent[T](
    session: Session,
    *,
    job_type: str,
    entity_type: str,
    entity_id: str,
    idempotency_key: str,
    fn: Callable[[], T],
    trace_id: str | None = None,
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

    Returns:
        JobResult。status="skipped" の場合 fn は呼ばれていない。
    """
    existing = session.query(JobRun).filter(JobRun.idempotency_key == idempotency_key).one_or_none()

    if existing is not None and existing.status == "succeeded":
        logger.info(
            "job_run_skipped_already_succeeded",
            job_type=job_type,
            idempotency_key=idempotency_key,
        )
        return JobResult(status="skipped", job_run=existing)

    job_run: JobRun
    if existing is not None:
        # started のまま残った(クラッシュ)か failed -> attempt を増やして再実行。
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
        # シークレットを含めないよう、例外メッセージのみを切り詰めて保存する。
        job_run.last_error = str(exc)[:_MAX_ERROR_LENGTH]
        session.flush()
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
