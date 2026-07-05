from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models.job_run import JobRun
from app.services.jobs import JobResult, run_idempotent


def test_run_idempotent_calls_fn_once_for_same_key(db_session: Session) -> None:
    calls: list[int] = []

    def _fn() -> str:
        calls.append(1)
        return "ok"

    first = run_idempotent(
        db_session,
        job_type="demo",
        entity_type="topic",
        entity_id="t-1",
        idempotency_key="demo:t-1",
        fn=_fn,
    )
    second = run_idempotent(
        db_session,
        job_type="demo",
        entity_type="topic",
        entity_id="t-1",
        idempotency_key="demo:t-1",
        fn=_fn,
    )

    assert len(calls) == 1
    assert first.status == "succeeded"
    assert second.status == "skipped"
    assert db_session.query(JobRun).filter(JobRun.idempotency_key == "demo:t-1").count() == 1


def test_run_idempotent_records_failure_then_retries_with_incremented_attempt(
    db_session: Session,
) -> None:
    attempts: list[int] = []

    def _failing_fn() -> None:
        attempts.append(1)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run_idempotent(
            db_session,
            job_type="demo",
            entity_type="topic",
            entity_id="t-2",
            idempotency_key="demo:t-2",
            fn=_failing_fn,
        )

    job_run = db_session.query(JobRun).filter(JobRun.idempotency_key == "demo:t-2").one()
    assert job_run.status == "failed"
    assert job_run.attempt == 1
    assert job_run.last_error is not None
    assert "boom" in job_run.last_error

    def _succeeding_fn() -> str:
        attempts.append(1)
        return "ok"

    result = run_idempotent(
        db_session,
        job_type="demo",
        entity_type="topic",
        entity_id="t-2",
        idempotency_key="demo:t-2",
        fn=_succeeding_fn,
    )

    assert len(attempts) == 2
    assert result.status == "succeeded"
    db_session.refresh(job_run)
    assert job_run.attempt == 2
    assert job_run.status == "succeeded"


def test_run_idempotent_reruns_stale_started_job(db_session: Session) -> None:
    """lease超過(クラッシュでstartedのまま残った)JobRunは再実行される(D-016)。"""
    stale = JobRun(
        job_type="demo",
        entity_type="topic",
        entity_id="t-3",
        idempotency_key="demo:t-3",
        status="started",
        attempt=1,
        # lease(デフォルト3600秒)を超過させ、クラッシュ残骸(stale)として扱わせる。
        started_at=datetime.utcnow() - timedelta(hours=2),
    )
    db_session.add(stale)
    db_session.flush()

    calls: list[int] = []

    def _fn() -> str:
        calls.append(1)
        return "ok"

    result = run_idempotent(
        db_session,
        job_type="demo",
        entity_type="topic",
        entity_id="t-3",
        idempotency_key="demo:t-3",
        fn=_fn,
    )

    assert len(calls) == 1
    assert result.status == "succeeded"
    assert result.job_run.attempt == 2


def test_run_idempotent_returns_in_progress_when_lease_active(db_session: Session) -> None:
    """lease有効期間内のstartedなJobRunは、他プロセスが実行中とみなしfnを呼ばない(D-016)。"""
    active = JobRun(
        job_type="demo",
        entity_type="topic",
        entity_id="t-4",
        idempotency_key="demo:t-4",
        status="started",
        attempt=1,
        started_at=datetime.utcnow(),
    )
    db_session.add(active)
    db_session.flush()

    calls: list[int] = []

    def _fn() -> str:
        calls.append(1)
        return "ok"

    result = run_idempotent(
        db_session,
        job_type="demo",
        entity_type="topic",
        entity_id="t-4",
        idempotency_key="demo:t-4",
        fn=_fn,
    )

    assert len(calls) == 0
    assert isinstance(result, JobResult)
    assert result.status == "in_progress"
    assert result.job_run.id == active.id
    assert result.job_run.attempt == 1
