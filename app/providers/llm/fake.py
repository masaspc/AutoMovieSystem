"""決定的なFake LLMプロバイダー(テスト・APIキー未設定環境向け: D-006)。

入力(operation/system_prompt/user_prompt/model_policy/idempotency_key)のSHA256を
シードに、response_schemaへ適合するデータを決定的に生成する。同一入力からは
常に同一出力を返す(実APIを一切呼ばない)。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.providers.llm.base import StructuredLLMResult, get_model_pricing

Generator = Callable[[bytes, str, str, type[BaseModel]], dict]


def _seed_bytes(*parts: str) -> bytes:
    joined = "␟".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).digest()


def _deterministic_int(seed: bytes, modulo: int, offset: int = 0) -> int:
    return offset + (int.from_bytes(seed[:8], "big") % modulo)


def _dummy_value_for_annotation(annotation: Any, seed: bytes, name: str) -> Any:
    origin = getattr(annotation, "__origin__", None)
    if annotation is str:
        return f"{name}-{seed.hex()[:8]}"
    if annotation is int:
        return _deterministic_int(seed, 100)
    if annotation is float:
        return float(_deterministic_int(seed, 100))
    if annotation is bool:
        return seed[0] % 2 == 0
    if origin is list:
        args = getattr(annotation, "__args__", (str,))
        item_type = args[0] if args else str
        return [_dummy_value_for_annotation(item_type, seed, f"{name}0")]
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return {
            field_name: _dummy_value_for_annotation(field.annotation, seed, field_name)
            for field_name, field in annotation.model_fields.items()
        }
    return f"{name}-{seed.hex()[:8]}"


def _default_generator(
    seed: bytes, operation: str, user_prompt: str, schema: type[BaseModel]
) -> dict:
    """response_schema のフィールド型から決定的なダミー値を組み立てる汎用ジェネレーター。"""
    return {
        name: _dummy_value_for_annotation(field.annotation, seed, name)
        for name, field in schema.model_fields.items()
    }


def _generate_script_content(
    seed: bytes, operation: str, user_prompt: str, schema: type[BaseModel]
) -> dict:
    """`generate_script` operation用の意味のある日本語ダミー台本(仕様§10構造)。"""
    suffix = seed.hex()[:6]
    return {
        "title_candidates": [
            f"知らないと損する話 #{suffix}",
            f"3分でわかる完全ガイド #{suffix}",
        ],
        "target_audience": "初めてこのテーマに触れる視聴者",
        "viewer_problem": "何から手を付ければよいか分からず時間を浪費している",
        "promised_outcome": "動画を見終える頃には次に取るべき行動が明確になる",
        "hook": f"実は多くの人が誤解しているポイントがあります({suffix})",
        "sections": [
            {
                "heading": "導入",
                "narration": "今日のテーマについて、まず全体像を整理します。",
                "visual_instruction": "タイトルテロップとテーマ画像を表示する。",
                "evidence_ids": [],
            },
            {
                "heading": "本編",
                "narration": "リサーチで確認できた事実をもとに要点を解説します。",
                "visual_instruction": "根拠となる出典を画面下部に表示する。",
                "evidence_ids": [],
            },
        ],
        "conclusion": "今回の内容を振り返り、次に取るべき行動をまとめます。",
        "call_to_action": "続きが気になる方はチャンネル登録して次回の更新をお待ちください。",
        "description": f"本動画では{suffix}に関するテーマを解説します。",
        "tags": ["解説", "初心者向け"],
        "chapters": ["導入", "本編", "まとめ"],
    }


def _classify_comment(
    seed: bytes, operation: str, user_prompt: str, schema: type[BaseModel]
) -> dict:
    """`classify_comment` operation用: 決定的にカテゴリを選択する。"""
    categories = ("question", "praise", "criticism", "spam", "suggestion")
    category = categories[_deterministic_int(seed, len(categories))]
    data = _default_generator(seed, operation, user_prompt, schema)
    if "category" in schema.model_fields:
        data["category"] = category
    return data


_GENERATORS: dict[str, Generator] = {
    "generate_script": _generate_script_content,
    "classify_comment": _classify_comment,
}


def register_generator(operation: str, generator: Generator) -> None:
    """operationごとの専用ジェネレーターを登録する(拡張・テスト用)。"""
    _GENERATORS[operation] = generator


class DeterministicFakeLLMProvider:
    """入力のSHA256から決定的に応答を生成するFake実装。実APIを一切呼ばない。"""

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
        seed = _seed_bytes(operation, system_prompt, user_prompt, model_policy, idempotency_key)
        generator = _GENERATORS.get(operation, _default_generator)
        data = generator(seed, operation, user_prompt, response_schema)
        try:
            response_schema.model_validate(data)
        except Exception:
            # 専用ジェネレーターがスキーマ不適合を返した場合は汎用ジェネレーターへフォールバック。
            data = _default_generator(seed, operation, user_prompt, response_schema)

        model = f"fake-{model_policy}"
        input_tokens = max(1, (len(system_prompt) + len(user_prompt)) // 2)
        output_tokens = max(1, len(str(data)) // 2)
        pricing = get_model_pricing(model)
        cost = (
            input_tokens * pricing.input_micro_usd_per_million
            + output_tokens * pricing.output_micro_usd_per_million
        ) // 1_000_000
        latency_ms = 1 + (seed[0] % 50)

        return StructuredLLMResult(
            data=data,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_micro_usd=cost,
            latency_ms=latency_ms,
            cached=False,
        )
