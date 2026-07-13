"""決定的なFake LLMプロバイダー(テスト・APIキー未設定環境向け: D-006)。

入力(operation/system_prompt/user_prompt/model_policy/idempotency_key)のSHA256を
シードに、response_schemaへ適合するデータを決定的に生成する。同一入力からは
常に同一出力を返す(実APIを一切呼ばない)。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.core.config import get_settings
from app.providers.llm.base import StructuredLLMResult, get_model_pricing

Generator = Callable[[bytes, str, str, type[BaseModel]], dict]

# app/services/scripts/generator.py の _build_repair_prompt が埋め込むマーカーと
# 「短い/長い」の目印文言(このFake実装専用の簡易パース。実プロバイダーは自然文で判断する)。
_REPAIR_JSON_MARKER = "[修復対象の台本(JSON)]\n"
_REPAIR_TOO_SHORT_MARKER = "より短いです"
_REPAIR_TOO_LONG_MARKER = "より長いです"
# 尺不足時に音声化対象のsectionへ加える決定的な補足文。
_REPAIR_EXPAND_FILLER = (
    "補足として具体例を挙げると、実務での活用場面は多岐にわたり、"
    "注意点や導入手順まで丁寧に確認しておくことで失敗を避けやすくなります。"
)
_SECTION_JSON_MARKER = "[対象セクションJSON]\n"


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
    second_section_dialogue = [
        {
            "speaker": "metan",
            "text": "確認できた根拠をもとに、要点を順番に説明します。",
            "emotion": "serious",
        },
        {
            "speaker": "zundamon",
            "text": "難しいところも、ひとつずつ見ていけば大丈夫なのだ。",
            "emotion": "happy",
        },
    ]
    configured_cast = {
        name.strip() for name in get_settings().DIALOGUE_CAST.split(",") if name.strip()
    }
    dialogue_enabled = get_settings().DIALOGUE_SCRIPT_ENABLED
    if "tsumugi" in configured_cast:
        second_section_dialogue.append(
            {
                "speaker": "tsumugi",
                "text": "ここは覚えておくと、次の判断がずっと楽になりますよ。",
                "emotion": "neutral",
            }
        )
    if not dialogue_enabled:
        second_section_dialogue = []

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
                "dialogue": [
                    {
                        "speaker": "zundamon",
                        "text": "今日はテーマの全体像を一緒に整理するのだ。",
                        "emotion": "happy",
                    },
                    {
                        "speaker": "metan",
                        "text": "まず結論から確認していきましょう。",
                        "emotion": "neutral",
                    },
                ]
                if dialogue_enabled
                else [],
                "visual_type": "key_point",
                "background_style": "card",
                "character_layout": "small_right",
                "visual_title": "今日できるようになること",
                "visual_bullets": ["全体像をつかむ", "重要ポイントを理解する"],
                "emphasis_words": ["全体像", "重要ポイント"],
            },
            {
                "heading": "本編",
                "narration": "リサーチで確認できた事実をもとに要点を解説します。",
                "visual_instruction": "根拠となる出典を画面下部に表示する。",
                "evidence_ids": [],
                "dialogue": second_section_dialogue,
                "visual_type": "code",
                "background_style": "editor",
                "character_layout": "small_left",
                "visual_title": "コードで確認",
                "code": 'print("Hello, Python!")',
                "highlight_lines": [1],
                "emphasis_words": ["print", "出力"],
            },
        ],
        "conclusion": "今回の内容を振り返り、次に取るべき行動をまとめます。",
        "call_to_action": "続きが気になる方はチャンネル登録して次回の更新をお待ちください。",
        "description": f"本動画では{suffix}に関するテーマを解説します。",
        "tags": ["解説", "初心者向け"],
        "chapters": ["導入", "本編", "まとめ"],
        "thumbnail_texts": [
            "えっ、5分で!?",
            "初心者の9割が誤解",
            f"完全解説{suffix[:2]}",
        ],
    }


def _repair_script_duration(
    seed: bytes, operation: str, user_prompt: str, schema: type[BaseModel]
) -> dict:
    """`repair_script_duration` operation用: 修復対象の台本JSONを伸縮させて返す。

    `evidence_ids` を含む構造は一切変更しない(短縮時もsection.evidence_idsはそのまま)。
    プロンプトから埋め込まれた元台本のJSONをそのまま解析できない場合は汎用生成にフォールバックする。
    """
    marker_index = user_prompt.find(_REPAIR_JSON_MARKER)
    if marker_index == -1:
        return _generate_script_content(seed, operation, user_prompt, schema)

    try:
        data = json.loads(user_prompt[marker_index + len(_REPAIR_JSON_MARKER) :])
    except (json.JSONDecodeError, ValueError):
        return _generate_script_content(seed, operation, user_prompt, schema)

    if _REPAIR_TOO_SHORT_MARKER in user_prompt:
        sections = data.get("sections", [])
        if sections:
            section = sections[-1]
            section["narration"] = f"{section.get('narration', '')}{_REPAIR_EXPAND_FILLER}"
            dialogue = section.get("dialogue", []) or []
            if dialogue:
                dialogue[-1]["text"] = f"{dialogue[-1].get('text', '')}{_REPAIR_EXPAND_FILLER}"
    elif _REPAIR_TOO_LONG_MARKER in user_prompt:
        for section in data.get("sections", []):
            narration = str(section.get("narration") or "")
            section["narration"] = narration[: max(1, len(narration) // 2)]
            for line in section.get("dialogue", []) or []:
                text = str(line.get("text") or "")
                line["text"] = text[: max(1, len(text) // 2)]

    try:
        schema.model_validate(data)
    except Exception:
        return _generate_script_content(seed, operation, user_prompt, schema)
    return data


def _regenerate_script_section(
    seed: bytes, operation: str, user_prompt: str, schema: type[BaseModel]
) -> dict:
    del seed, operation, schema
    marker_index = user_prompt.find(_SECTION_JSON_MARKER)
    if marker_index == -1:
        return {}
    try:
        data = json.loads(user_prompt[marker_index + len(_SECTION_JSON_MARKER) :])
    except json.JSONDecodeError:
        return {}
    data["narration"] = f"{data.get('narration', '')} より分かりやすい表現に改善しました。"
    for line in data.get("dialogue", []) or []:
        line["text"] = f"{line.get('text', '')} 分かりやすく補足します。"
    return data


def _generate_curriculum(
    seed: bytes, operation: str, user_prompt: str, schema: type[BaseModel]
) -> dict:
    del seed, operation, schema
    match = re.search(r"episode_count=(\d+)", user_prompt)
    count = int(match.group(1)) if match else 5
    episodes = []
    for position in range(1, count + 1):
        previous_concept = f"第{position - 1}回の基礎" if position > 1 else ""
        episodes.append(
            {
                "position": position,
                "title": f"基礎ステップ{position}",
                "summary": f"第{position}回で必要な基礎を順番に学びます。",
                "learning_objectives": [f"ステップ{position}を自分で説明できる"],
                "prerequisite_positions": [position - 1] if position > 1 else [],
                "new_concepts": [f"概念{position}"],
                "review_concepts": [previous_concept] if previous_concept else [],
                "excluded_concepts": [f"概念{position + 1}"] if position < count else [],
                "demo_outline": f"概念{position}の短い実演",
                "exercise_outline": f"概念{position}を使う練習",
                "next_episode_bridge": "次の概念につながる疑問を提示します。",
                "target_duration_seconds": 300,
            }
        )
    return {"episodes": episodes}


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


def _self_review(
    seed: bytes, operation: str, user_prompt: str, schema: type[BaseModel]
) -> dict[str, Any]:
    """`self_review` operation用の決定的な振り返りレポート。"""
    del seed, operation, user_prompt, schema
    return {
        "good_points": ["フックで結論を先出しできていた"],
        "bad_points": ["コード画面が長く続く場面で離脱が見られた"],
        "lessons": [
            {
                "finding": "コード画面が長いと維持率が下がる",
                "recommended_action": "コード解説は1画面30秒以内に分割してください",
            },
            {
                "finding": "確認要素のある場面は視聴をつなぎやすい",
                "recommended_action": "中盤に確認クイズを1問入れてください",
            },
        ],
    }


# content_review用の誇張・断定NGワード(inspector.py DEFAULT_NG_WORDSと同趣旨)。
_CONTENT_REVIEW_NG_WORDS: tuple[str, ...] = (
    "絶対に儲かる",
    "必ず成功",
    "誰でも稼げる",
    "確実に稼げる",
)


def _review_content(seed: bytes, operation: str, user_prompt: str, schema: type[BaseModel]) -> dict:
    """`review_content` operation用: 決定的に「問題なし」を返し、NGワードがあれば
    該当Findingを返す簡易ロジック。
    """
    findings = [
        {
            "code": "llm_flagged_exaggeration",
            "severity": "blocking",
            "message": f"誇張・断定表現の疑いがあります: {word}",
            "detail": None,
        }
        for word in _CONTENT_REVIEW_NG_WORDS
        if word in user_prompt
    ]
    return {"findings": findings, "passed": len(findings) == 0}


_GENERATORS: dict[str, Generator] = {
    "generate_script": _generate_script_content,
    "repair_script_duration": _repair_script_duration,
    "regenerate_script_section": _regenerate_script_section,
    "generate_curriculum": _generate_curriculum,
    "classify_comment": _classify_comment,
    "self_review": _self_review,
    "review_content": _review_content,
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
