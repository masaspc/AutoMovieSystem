"""LLM呼び出しの唯一の入口(仕様§9・D-011・ADR-0007)。

- `LLMCache` (operation, prompt_version, input_hash) ヒット時はプロバイダーを呼ばず、
  UsageRecordも増やさない(二重計上防止)。
- 予算制御: 呼び出し前に `BudgetLedger`(daily/monthly)へ見積額を条件付きUPDATEで
  reserve → 成功時 commit(実額) / 失敗時 release。100%超過で `BudgetExceededError`
  (AI処理のみ停止)。80%超で警告ログ。
- 見積: operation別の max_input_tokens/max_output_tokens/max_cost_micro_usd を超過
  したら `CostLimitExceededError`(高いモデルへ自動昇格しない)。
- 構造化出力のスキーマ不適合時の修復リトライは最大1回(仕様§17)。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.budget_ledger import BudgetLedger
from app.models.llm_cache import LLMCache
from app.models.usage_record import UsageRecord
from app.providers.llm.base import (
    LLMProvider,
    LLMResponseSchemaError,
    StructuredLLMResult,
    get_model_pricing,
    resolve_model_id,
)
from app.providers.llm.local_openai import resolve_local_model_id

logger = get_logger(__name__)


def _resolve_provider_name(model_policy: str, settings: Settings) -> str:
    """model_policy に対応する実際のプロバイダー名を設定から解決する。"""
    mapping = {
        "low": settings.LLM_PROVIDER_LOW or settings.LLM_PROVIDER,
        "mid": settings.LLM_PROVIDER_MID or settings.LLM_PROVIDER,
        "high": settings.LLM_PROVIDER_HIGH or settings.LLM_PROVIDER,
    }
    try:
        return mapping[model_policy]
    except KeyError as exc:
        raise ValueError(f"Unknown model_policy: {model_policy!r}") from exc


def _resolve_billing_model_id(model_policy: str, provider_name: str, settings: Settings) -> str:
    """予算予約・失敗記録に使う、実プロバイダーに対応したモデルIDを返す。"""
    if provider_name == "local":
        return resolve_local_model_id(model_policy, settings)
    return resolve_model_id(model_policy, settings)


_BUDGET_WARNING_THRESHOLD = 0.8


class BudgetExceededError(RuntimeError):
    """日次/月次いずれかの予算が100%に達した場合。AI処理のみ停止する(非AI処理は継続)。"""


class CostLimitExceededError(RuntimeError):
    """operationごとのトークン数/コスト見積が上限を超えた場合。保留しレビュー対象にする。"""


@dataclass(frozen=True)
class OperationLimits:
    max_input_tokens: int
    max_output_tokens: int
    max_cost_micro_usd: int


# operation別の上限(仕様§17・architecture.md モデルルーティングポリシー)。
OPERATION_LIMITS: dict[str, OperationLimits] = {
    "generate_script": OperationLimits(
        max_input_tokens=8_000, max_output_tokens=4_000, max_cost_micro_usd=3_000_000
    ),
    # 長尺修復は生成済みの台本JSON全体を入力へ含めるため、通常生成より入力枠が必要。
    # 既定値(4,000)へフォールバックさせると5分前後の台本をLLM呼び出し前に拒否してしまう。
    "repair_script_duration": OperationLimits(
        max_input_tokens=12_000, max_output_tokens=6_000, max_cost_micro_usd=4_000_000
    ),
    "classify_comment": OperationLimits(
        max_input_tokens=2_000, max_output_tokens=500, max_cost_micro_usd=200_000
    ),
    "self_review": OperationLimits(
        max_input_tokens=6_000, max_output_tokens=2_000, max_cost_micro_usd=1_000_000
    ),
    "growth_quality_review": OperationLimits(
        max_input_tokens=16_000, max_output_tokens=3_000, max_cost_micro_usd=1_000_000
    ),
    "growth_quality_rewrite": OperationLimits(
        max_input_tokens=20_000, max_output_tokens=8_000, max_cost_micro_usd=2_000_000
    ),
}
_DEFAULT_OPERATION_LIMITS = OperationLimits(
    max_input_tokens=4_000, max_output_tokens=2_000, max_cost_micro_usd=1_000_000
)


def _operation_limits(operation: str) -> OperationLimits:
    return OPERATION_LIMITS.get(operation, _DEFAULT_OPERATION_LIMITS)


def _estimate_input_tokens(text: str) -> int:
    """文字数からトークン数を決定的に見積もる(日本語想定: 概ね1トークン≒2文字)。"""
    return max(1, (len(text) + 1) // 2)


def _compute_cost_micro_usd(model: str, input_tokens: int, output_tokens: int) -> int:
    pricing = get_model_pricing(model)
    input_cost = (input_tokens * pricing.input_micro_usd_per_million) // 1_000_000
    output_cost = (output_tokens * pricing.output_micro_usd_per_million) // 1_000_000
    return input_cost + output_cost


def _period_keys(now: datetime) -> tuple[str, str]:
    """UTC基準の期間キー: daily -> 'YYYY-MM-DD', monthly -> 'YYYY-MM'。"""
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")


def _get_or_create_ledger(
    session: Session, *, period_type: str, period_key: str, default_budget_micro_usd: int
) -> BudgetLedger:
    existing = (
        session.query(BudgetLedger)
        .filter(BudgetLedger.period_type == period_type, BudgetLedger.period_key == period_key)
        .one_or_none()
    )
    if existing is not None:
        return existing

    ledger = BudgetLedger(
        period_type=period_type, period_key=period_key, budget_micro_usd=default_budget_micro_usd
    )
    try:
        with session.begin_nested():
            session.add(ledger)
            session.flush()
    except IntegrityError:
        session.expunge(ledger)
        winner = (
            session.query(BudgetLedger)
            .filter(BudgetLedger.period_type == period_type, BudgetLedger.period_key == period_key)
            .one()
        )
        return winner
    return ledger


def _reserve(session: Session, ledger: BudgetLedger, amount_micro_usd: int) -> None:
    """アトミックな条件付きUPDATEで予約する。残余不足なら `BudgetExceededError`。"""
    stmt = (
        update(BudgetLedger)
        .where(BudgetLedger.id == ledger.id)
        .where(
            (BudgetLedger.committed_micro_usd + BudgetLedger.reserved_micro_usd + amount_micro_usd)
            <= BudgetLedger.budget_micro_usd
        )
        .values(reserved_micro_usd=BudgetLedger.reserved_micro_usd + amount_micro_usd)
    )
    result = session.execute(stmt)
    if result.rowcount == 0:  # type: ignore[attr-defined]
        logger.warning(
            "ai_budget_exceeded",
            period_type=ledger.period_type,
            period_key=ledger.period_key,
            requested_micro_usd=amount_micro_usd,
        )
        raise BudgetExceededError(
            f"budget exceeded: period_type={ledger.period_type} period_key={ledger.period_key}"
        )
    session.refresh(ledger)
    _warn_if_over_threshold(ledger)


def _release(session: Session, ledger: BudgetLedger, amount_micro_usd: int) -> None:
    stmt = (
        update(BudgetLedger)
        .where(BudgetLedger.id == ledger.id)
        .values(reserved_micro_usd=BudgetLedger.reserved_micro_usd - amount_micro_usd)
    )
    session.execute(stmt)
    session.refresh(ledger)


def _commit(
    session: Session, ledger: BudgetLedger, *, reserved_amount: int, actual_amount: int
) -> None:
    stmt = (
        update(BudgetLedger)
        .where(BudgetLedger.id == ledger.id)
        .values(
            reserved_micro_usd=BudgetLedger.reserved_micro_usd - reserved_amount,
            committed_micro_usd=BudgetLedger.committed_micro_usd + actual_amount,
        )
    )
    session.execute(stmt)
    session.refresh(ledger)
    _warn_if_over_threshold(ledger)


def _warn_if_over_threshold(ledger: BudgetLedger) -> None:
    if ledger.budget_micro_usd <= 0:
        return
    used_ratio = (ledger.committed_micro_usd + ledger.reserved_micro_usd) / ledger.budget_micro_usd
    if used_ratio >= _BUDGET_WARNING_THRESHOLD:
        logger.warning(
            "ai_budget_warning_threshold",
            period_type=ledger.period_type,
            period_key=ledger.period_key,
            used_ratio=round(used_ratio, 4),
        )


def _next_seq(session: Session, job_run_id: str | None) -> int:
    if job_run_id is None:
        return 0
    current_max = (
        session.query(func.max(UsageRecord.seq))
        .filter(UsageRecord.job_run_id == job_run_id)
        .scalar()
    )
    return 0 if current_max is None else current_max + 1


def _record_usage(
    session: Session,
    *,
    provider_name: str,
    operation: str,
    prompt_version: str,
    input_hash: str,
    model: str,
    job_run_id: str | None,
    success: bool,
    error_type: str | None,
    input_tokens: int,
    output_tokens: int,
    cost_micro_usd: int,
    latency_ms: int,
) -> UsageRecord:
    record = UsageRecord(
        provider=provider_name,
        model=model,
        operation=operation,
        prompt_version=prompt_version,
        input_hash=input_hash,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_micro_usd=cost_micro_usd,
        latency_ms=latency_ms,
        success=success,
        error_type=error_type,
        job_run_id=job_run_id,
        seq=_next_seq(session, job_run_id),
    )
    session.add(record)
    session.flush()
    return record


def _store_cache(
    session: Session,
    *,
    operation: str,
    prompt_version: str,
    input_hash: str,
    model: str,
    data: dict,
) -> None:
    cache_row = LLMCache(
        operation=operation,
        prompt_version=prompt_version,
        input_hash=input_hash,
        model=model,
        response_json=json.dumps(data, ensure_ascii=False),
    )
    try:
        with session.begin_nested():
            session.add(cache_row)
            session.flush()
    except IntegrityError:
        # 並行実行で他が先に同じキャッシュキーを書き込んだ -> 自分の結果は既に呼び出し元へ
        # 返せているのでそのまま無視する(重複キャッシュ行を作らない)。
        session.expunge(cache_row)


async def _generate_with_repair(
    provider: LLMProvider,
    *,
    operation: str,
    system_prompt: str,
    user_prompt: str,
    response_schema: type[BaseModel],
    model_policy: str,
    idempotency_key: str,
) -> StructuredLLMResult:
    """構造化出力を取得し、スキーマ不適合なら最大1回だけ修復リトライする(仕様§17)。"""
    result = await provider.generate_structured(
        operation=operation,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_schema=response_schema,
        model_policy=model_policy,
        idempotency_key=idempotency_key,
    )
    try:
        response_schema.model_validate(result.data)
        return result
    except ValidationError as exc:
        logger.warning("llm_schema_validation_failed_retrying", operation=operation, error=str(exc))

    repair_prompt = (
        f"{user_prompt}\n\n"
        "[REPAIR] 前回の出力はJSON Schemaに適合しませんでした。"
        "スキーマに厳密に適合するJSONのみを出力してください。"
    )
    retried = await provider.generate_structured(
        operation=operation,
        system_prompt=system_prompt,
        user_prompt=repair_prompt,
        response_schema=response_schema,
        model_policy=model_policy,
        idempotency_key=f"{idempotency_key}:repair",
    )
    try:
        response_schema.model_validate(retried.data)
    except ValidationError as exc:
        raise LLMResponseSchemaError(
            f"schema validation failed after repair retry (operation={operation}): {exc}"
        ) from exc
    return retried


async def call_llm(
    session: Session,
    provider: LLMProvider,
    *,
    operation: str,
    prompt_version: str,
    system_prompt: str,
    user_prompt: str,
    response_schema: type[BaseModel],
    model_policy: str,
    idempotency_key: str,
    job_run_id: str | None,
    provider_name: str | None = None,
    settings: Settings | None = None,
) -> StructuredLLMResult:
    """LLM呼び出しの唯一の入口。キャッシュ・予算・使用量記録をすべてここで行う。"""
    settings = settings or get_settings()
    provider_name = provider_name or _resolve_provider_name(model_policy, settings)

    input_hash = hashlib.sha256(
        "␟".join([operation, prompt_version, model_policy, system_prompt, user_prompt]).encode(
            "utf-8"
        )
    ).hexdigest()

    cached_row = (
        session.query(LLMCache)
        .filter(
            LLMCache.operation == operation,
            LLMCache.prompt_version == prompt_version,
            LLMCache.input_hash == input_hash,
        )
        .one_or_none()
    )
    if cached_row is not None:
        logger.info("llm_cache_hit", operation=operation, prompt_version=prompt_version)
        return StructuredLLMResult(
            data=json.loads(cached_row.response_json),
            model=cached_row.model,
            input_tokens=0,
            output_tokens=0,
            estimated_cost_micro_usd=0,
            latency_ms=0,
            cached=True,
        )

    limits = _operation_limits(operation)
    model_id = _resolve_billing_model_id(model_policy, provider_name, settings)
    estimated_input_tokens = _estimate_input_tokens(system_prompt + user_prompt)
    if estimated_input_tokens > limits.max_input_tokens:
        raise CostLimitExceededError(
            f"estimated input tokens {estimated_input_tokens} exceed limit "
            f"{limits.max_input_tokens} for operation={operation}"
        )
    estimated_cost = (
        0
        if provider_name == "local"
        else _compute_cost_micro_usd(model_id, estimated_input_tokens, limits.max_output_tokens)
    )
    if estimated_cost > limits.max_cost_micro_usd:
        raise CostLimitExceededError(
            f"estimated cost {estimated_cost} exceeds limit {limits.max_cost_micro_usd} "
            f"for operation={operation}"
        )

    now = datetime.now(UTC)
    daily_key, monthly_key = _period_keys(now)
    daily_ledger = _get_or_create_ledger(
        session,
        period_type="daily",
        period_key=daily_key,
        default_budget_micro_usd=settings.DAILY_AI_BUDGET_MICRO_USD,
    )
    monthly_ledger = _get_or_create_ledger(
        session,
        period_type="monthly",
        period_key=monthly_key,
        default_budget_micro_usd=settings.MONTHLY_AI_BUDGET_MICRO_USD,
    )

    _reserve(session, daily_ledger, estimated_cost)
    try:
        _reserve(session, monthly_ledger, estimated_cost)
    except BudgetExceededError:
        _release(session, daily_ledger, estimated_cost)
        raise

    try:
        result = await _generate_with_repair(
            provider,
            operation=operation,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=response_schema,
            model_policy=model_policy,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        _release(session, daily_ledger, estimated_cost)
        _release(session, monthly_ledger, estimated_cost)
        _record_usage(
            session,
            provider_name=provider_name,
            operation=operation,
            prompt_version=prompt_version,
            input_hash=input_hash,
            model=model_id,
            job_run_id=job_run_id,
            success=False,
            error_type=type(exc).__name__,
            input_tokens=0,
            output_tokens=0,
            cost_micro_usd=0,
            latency_ms=0,
        )
        raise

    actual_cost = result.estimated_cost_micro_usd
    _commit(session, daily_ledger, reserved_amount=estimated_cost, actual_amount=actual_cost)
    _commit(session, monthly_ledger, reserved_amount=estimated_cost, actual_amount=actual_cost)

    _record_usage(
        session,
        provider_name=provider_name,
        operation=operation,
        prompt_version=prompt_version,
        input_hash=input_hash,
        model=result.model,
        job_run_id=job_run_id,
        success=True,
        error_type=None,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_micro_usd=actual_cost,
        latency_ms=result.latency_ms,
    )
    _store_cache(
        session,
        operation=operation,
        prompt_version=prompt_version,
        input_hash=input_hash,
        model=result.model,
        data=result.data,
    )

    return result
