"""model_policy別プロバイダールーティング(仕様§9・D-020)。

`LLM_PROVIDER_LOW`/`LLM_PROVIDER_MID`/`LLM_PROVIDER_HIGH` が互いに異なる場合のみ
`app/providers/llm/factory.py` の `get_llm_provider` が本クラスを返す。全て同一
(または未設定)なら従来どおり単一プロバイダーを返す(D-006の後方互換を維持)。
"""

from __future__ import annotations

from pydantic import BaseModel

from app.providers.llm.base import MODEL_POLICIES, LLMProvider, StructuredLLMResult


class RoutingLLMProvider:
    """`LLMProvider` Protocol を満たし、model_policyごとに異なるプロバイダーへ委譲する。"""

    def __init__(self, providers_by_policy: dict[str, LLMProvider]) -> None:
        missing = set(MODEL_POLICIES) - set(providers_by_policy)
        if missing:
            raise ValueError(
                "RoutingLLMProvider requires a provider for every model_policy; "
                f"missing: {sorted(missing)}"
            )
        self._providers_by_policy = dict(providers_by_policy)

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
        provider = self._providers_by_policy.get(model_policy)
        if provider is None:
            raise ValueError(
                f"Unknown model_policy: {model_policy!r} (expected one of {MODEL_POLICIES})"
            )
        return await provider.generate_structured(
            operation=operation,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=response_schema,
            model_policy=model_policy,
            idempotency_key=idempotency_key,
        )
