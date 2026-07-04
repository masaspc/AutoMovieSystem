from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.budget_ledger import BudgetLedger
from app.models.job_run import JobRun
from app.models.usage_record import UsageRecord


def test_budget_ledger_unique_period(db_session: Session) -> None:
    db_session.add(
        BudgetLedger(period_type="daily", period_key="2026-07-04", budget_micro_usd=5_000_000)
    )
    db_session.flush()

    db_session.add(
        BudgetLedger(period_type="daily", period_key="2026-07-04", budget_micro_usd=5_000_000)
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_budget_ledger_allows_different_period_type_same_key(db_session: Session) -> None:
    db_session.add(
        BudgetLedger(period_type="daily", period_key="2026-07", budget_micro_usd=5_000_000)
    )
    db_session.flush()
    db_session.add(
        BudgetLedger(period_type="monthly", period_key="2026-07", budget_micro_usd=100_000_000)
    )
    db_session.flush()  # 例外が出ないこと


def test_usage_record_unique_job_run_and_seq(db_session: Session) -> None:
    job_run = JobRun(
        job_type="generate_script",
        entity_type="topic",
        entity_id="t-1",
        idempotency_key="generate_script:t-1",
        status="succeeded",
    )
    db_session.add(job_run)
    db_session.flush()

    db_session.add(
        UsageRecord(
            provider="fake",
            model="fake-model",
            operation="generate_script",
            input_tokens=10,
            output_tokens=20,
            estimated_cost_micro_usd=100,
            job_run_id=job_run.id,
            seq=0,
        )
    )
    db_session.flush()

    db_session.add(
        UsageRecord(
            provider="fake",
            model="fake-model",
            operation="generate_script",
            input_tokens=10,
            output_tokens=20,
            estimated_cost_micro_usd=100,
            job_run_id=job_run.id,
            seq=0,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_usage_record_allows_multiple_null_job_run_id(db_session: Session) -> None:
    """job_run_id が NULL の場合はUNIQUE制約が働かない(NULLは非等価)。"""
    db_session.add(
        UsageRecord(
            provider="fake",
            model="fake-model",
            operation="classify_comment",
            input_tokens=1,
            output_tokens=1,
            estimated_cost_micro_usd=1,
            job_run_id=None,
            seq=0,
        )
    )
    db_session.flush()
    db_session.add(
        UsageRecord(
            provider="fake",
            model="fake-model",
            operation="classify_comment",
            input_tokens=1,
            output_tokens=1,
            estimated_cost_micro_usd=1,
            job_run_id=None,
            seq=0,
        )
    )
    db_session.flush()  # 例外が出ないこと
