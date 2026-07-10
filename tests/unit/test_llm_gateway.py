from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.budget_ledger import BudgetLedger
from app.models.llm_cache import LLMCache
from app.models.usage_record import UsageRecord
from app.providers.llm.base import StructuredLLMResult
from app.services import llm_gateway
from app.services.llm_gateway import (
    BudgetExceededError,
    CostLimitExceededError,
    call_llm,
)


class _DummySchema(BaseModel):
    value: str


class _StubProvider:
    """テスト用の最小限のLLMProvider実装(実APIを呼ばない)。"""

    def __init__(
        self,
        responses: list[dict[str, Any]],
        *,
        model: str = "stub-model",
        cost_micro_usd: int = 100,
    ) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.model = model
        self._cost_micro_usd = cost_micro_usd

    async def generate_structured(
        self,
        *,
        operation: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[BaseModel],
        model_policy: str,
        idempotency_key: str,
    ) -> StructuredLLMResult:
        self.calls += 1
        data = self._responses.pop(0)
        return StructuredLLMResult(
            data=data,
            model=self.model,
            input_tokens=10,
            output_tokens=10,
            estimated_cost_micro_usd=self._cost_micro_usd,
            latency_ms=1,
            cached=False,
        )


class _FailingProvider:
    async def generate_structured(self, **_kwargs: Any) -> StructuredLLMResult:
        raise RuntimeError("provider failure")


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_call_llm_records_usage_and_caches(db_session: Session) -> None:
    provider = _StubProvider([{"value": "ok"}])

    result = _run(
        call_llm(
            db_session,
            provider,
            operation="generate_script",
            prompt_version="v1",
            system_prompt="sys",
            user_prompt="usr",
            response_schema=_DummySchema,
            model_policy="low",
            idempotency_key="key-1",
            job_run_id=None,
        )
    )

    assert result.cached is False
    assert provider.calls == 1
    assert db_session.query(UsageRecord).count() == 1
    assert db_session.query(LLMCache).count() == 1


def test_call_llm_second_call_hits_cache_and_skips_usage(db_session: Session) -> None:
    provider = _StubProvider([{"value": "ok"}])

    _run(
        call_llm(
            db_session,
            provider,
            operation="generate_script",
            prompt_version="v1",
            system_prompt="sys",
            user_prompt="usr",
            response_schema=_DummySchema,
            model_policy="low",
            idempotency_key="key-1",
            job_run_id=None,
        )
    )
    result2 = _run(
        call_llm(
            db_session,
            provider,
            operation="generate_script",
            prompt_version="v1",
            system_prompt="sys",
            user_prompt="usr",
            response_schema=_DummySchema,
            model_policy="low",
            idempotency_key="key-1",
            job_run_id=None,
        )
    )

    assert result2.cached is True
    assert provider.calls == 1  # プロバイダーは2回目は呼ばれない
    assert db_session.query(UsageRecord).count() == 1  # 二重計上なし


def test_call_llm_budget_exceeded_raises(db_session: Session) -> None:
    provider = _StubProvider([{"value": "ok"}])
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        DAILY_AI_BUDGET_MICRO_USD=1,
        MONTHLY_AI_BUDGET_MICRO_USD=1,
    )

    with pytest.raises(BudgetExceededError):
        _run(
            call_llm(
                db_session,
                provider,
                operation="generate_script",
                prompt_version="v1",
                system_prompt="sys",
                user_prompt="usr",
                response_schema=_DummySchema,
                model_policy="low",
                idempotency_key="key-budget",
                job_run_id=None,
                settings=settings,
            )
        )

    ledger = db_session.query(BudgetLedger).filter(BudgetLedger.period_type == "daily").one()
    assert ledger.reserved_micro_usd == 0
    assert ledger.committed_micro_usd == 0
    assert provider.calls == 0


def test_call_llm_warns_at_80_percent_without_raising(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 見積コスト(reserve時点)が予算の80%を超えるよう予算を調整し、警告ログが
    # 出ること・例外にならないことを検証する(100%は超えない)。
    provider = _StubProvider([{"value": "ok"}])
    limits = llm_gateway.OPERATION_LIMITS["generate_script"]
    estimate = llm_gateway._compute_cost_micro_usd(
        "claude-sonnet-5",
        llm_gateway._estimate_input_tokens("sys" + "usr"),
        limits.max_output_tokens,
    )
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        DAILY_AI_BUDGET_MICRO_USD=int(estimate / 0.9) or 1,
        MONTHLY_AI_BUDGET_MICRO_USD=100_000_000,
    )

    warnings: list[tuple[str, dict[str, Any]]] = []

    def _capture_warning(event: str, **kwargs: Any) -> None:
        warnings.append((event, kwargs))

    monkeypatch.setattr(llm_gateway.logger, "warning", _capture_warning)

    _run(
        call_llm(
            db_session,
            provider,
            operation="generate_script",
            prompt_version="v1",
            system_prompt="sys",
            user_prompt="usr",
            response_schema=_DummySchema,
            model_policy="mid",
            idempotency_key="key-warn",
            job_run_id=None,
            settings=settings,
        )
    )

    events = [event for event, _ in warnings]
    assert "ai_budget_warning_threshold" in events
    assert "ai_budget_exceeded" not in events


def test_call_llm_cost_limit_exceeded_before_budget_check(db_session: Session) -> None:
    provider = _StubProvider([{"value": "ok"}])

    with pytest.raises(CostLimitExceededError):
        _run(
            call_llm(
                db_session,
                provider,
                operation="generate_script",
                prompt_version="v1",
                system_prompt="s" * 100_000,
                user_prompt="u" * 100_000,
                response_schema=_DummySchema,
                model_policy="low",
                idempotency_key="key-cost-limit",
                job_run_id=None,
            )
        )
    assert provider.calls == 0
    assert db_session.query(BudgetLedger).count() == 0  # 予算確認より前に保留される


def test_call_llm_failure_releases_reservation_and_records_failed_usage(
    db_session: Session,
) -> None:
    provider = _FailingProvider()

    with pytest.raises(RuntimeError):
        _run(
            call_llm(
                db_session,
                provider,
                operation="generate_script",
                prompt_version="v1",
                system_prompt="sys",
                user_prompt="usr",
                response_schema=_DummySchema,
                model_policy="low",
                idempotency_key="key-fail",
                job_run_id=None,
            )
        )

    ledger = db_session.query(BudgetLedger).filter(BudgetLedger.period_type == "daily").one()
    assert ledger.reserved_micro_usd == 0
    assert ledger.committed_micro_usd == 0

    usage = db_session.query(UsageRecord).one()
    assert usage.success is False
    assert usage.error_type == "RuntimeError"


def test_call_llm_repairs_once_on_schema_violation(db_session: Session) -> None:
    provider = _StubProvider([{"value": 123}, {"value": "ok"}])  # 1回目は型不一致

    result = _run(
        call_llm(
            db_session,
            provider,
            operation="generate_script",
            prompt_version="v1",
            system_prompt="sys",
            user_prompt="usr",
            response_schema=_DummySchema,
            model_policy="low",
            idempotency_key="key-repair",
            job_run_id=None,
        )
    )

    assert provider.calls == 2
    assert result.data == {"value": "ok"}


def test_call_llm_records_local_provider_with_zero_cost(db_session: Session) -> None:
    """LLM_PROVIDER=local でも UsageRecord.provider=="local"・cost==0 で記録される。"""
    provider = _StubProvider([{"value": "ok"}], model="qwen3:32b", cost_micro_usd=0)

    result = _run(
        call_llm(
            db_session,
            provider,
            operation="generate_script",
            prompt_version="v1",
            system_prompt="sys",
            user_prompt="usr",
            response_schema=_DummySchema,
            model_policy="mid",
            idempotency_key="key-local",
            job_run_id=None,
            provider_name="local",
        )
    )

    assert result.cached is False
    usage = db_session.query(UsageRecord).one()
    assert usage.provider == "local"
    assert usage.model == "qwen3:32b"
    assert usage.estimated_cost_micro_usd == 0


def test_call_llm_resolves_policy_local_provider_before_budget_reservation(
    db_session: Session,
) -> None:
    """ポリシー経由のlocalは、既定provider/Claude料金で予算予約してはならない。"""
    provider = _StubProvider([{"value": "ok"}], model="qwen3:8b", cost_micro_usd=0)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        LLM_PROVIDER="fake",
        LLM_PROVIDER_LOW="local",
        DAILY_AI_BUDGET_MICRO_USD=1,
        MONTHLY_AI_BUDGET_MICRO_USD=1,
    )

    _run(
        call_llm(
            db_session,
            provider,
            operation="generate_script",
            prompt_version="v1",
            system_prompt="sys",
            user_prompt="usr",
            response_schema=_DummySchema,
            model_policy="low",
            idempotency_key="key-policy-local",
            job_run_id=None,
            settings=settings,
        )
    )

    usage = db_session.query(UsageRecord).one()
    daily_ledger = db_session.query(BudgetLedger).filter(BudgetLedger.period_type == "daily").one()
    assert usage.provider == "local"
    assert usage.model == "qwen3:8b"
    assert daily_ledger.committed_micro_usd == 0


def test_call_llm_raises_schema_error_if_repair_also_fails(db_session: Session) -> None:
    provider = _StubProvider([{"bogus": 1}, {"bogus": 2}])

    from app.providers.llm.base import LLMResponseSchemaError

    with pytest.raises(LLMResponseSchemaError):
        _run(
            call_llm(
                db_session,
                provider,
                operation="generate_script",
                prompt_version="v1",
                system_prompt="sys",
                user_prompt="usr",
                response_schema=_DummySchema,
                model_policy="low",
                idempotency_key="key-repair-fail",
                job_run_id=None,
            )
        )
    assert provider.calls == 2
