"""動画ごとの台本構成バリエーションを決定論的に選ぶ。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

HOOK_STYLES: dict[str, str] = {
    "衝撃数字型": "根拠のある印象的な数字を冒頭に示し、その意味をすぐ説明する。",
    "問いかけ型": "視聴者が自分事として答えたくなる短い問いから始める。",
    "失敗談型": "典型的な失敗場面を一つ示し、回避策への期待を作る。",
    "結論先出し型": "動画で得られる結論を最初に明示し、理由を本編で解き明かす。",
    "あるある型": "対象視聴者が経験しやすい状況を具体的に描き、共感から本題へ入る。",
}

CORNER_STYLES: dict[str, str] = {
    "クイズ": "本編の理解を確かめる一問クイズを挿入し、回答後に理由も説明する。",
    "雑学": "本題に直結する短いトリビアを一つ挿入し、脱線せず学びへ戻す。",
    "あるある": "視聴者が共感できる具体的な『あるある』を掛け合いで紹介する。",
    "視聴者Q&A風": "視聴者から届きそうな質問を提示し、登場人物が簡潔に回答する。",
    "3選リスト": "重要点を『3選』として整理し、項目ごとの差を明確にする。",
    "たとえ話チャレンジ": "難しい概念を身近なたとえに置き換え、限界や注意点も補足する。",
}

TSUKKOMI_DENSITIES: dict[str, str] = {
    "high": "全セクションに自然なボケとツッコミを一往復ほど入れ、説明を邪魔しない。",
    "medium": "導入と重要な転換点を中心にボケとツッコミを入れる。",
    "low": "要所だけ短いツッコミを入れ、解説の明瞭さを優先する。",
}

BRIDGE_STYLES: dict[str, str] = {
    "予告型": "節末で次に分かることを一文で予告し、続きを見る理由を作る。",
    "疑問残し型": "節末に未解決の疑問を一つ残し、次節の冒頭で答える。",
    "意外性型": "次の内容が直前の常識をどう覆すかを短く示してつなぐ。",
    "実践誘導型": "説明から具体例・実演へ移ることを宣言し、テンポを切り替える。",
}


@dataclass(frozen=True)
class VarietyPlan:
    hook_style: str
    corners: list[str]
    tsukkomi_density: str
    bridge_style: str


def _pick(items: list[str], value: int) -> str:
    return items[value % len(items)]


def pick_variety_plan(seed: str, *, template: str) -> VarietyPlan:
    """同一seed+templateから常に同じ構成プランを選ぶ。"""
    digest = hashlib.sha256(f"variety:{seed}:{template}".encode()).digest()
    hooks = list(HOOK_STYLES)
    densities = list(TSUKKOMI_DENSITIES)
    bridges = list(BRIDGE_STYLES)
    corners = list(CORNER_STYLES)
    if template == "news_commentary":
        corners = [corner for corner in corners if corner != "クイズ"]

    corner_count = digest[3] % 2 if template == "shorts" else 1 + digest[3] % 2

    start = digest[4] % len(corners)
    step = 1 + digest[5] % (len(corners) - 1)
    selected_corners: list[str] = []
    offset = 0
    while len(selected_corners) < min(corner_count, len(corners)):
        candidate = corners[(start + offset * step) % len(corners)]
        if candidate not in selected_corners:
            selected_corners.append(candidate)
        offset += 1

    return VarietyPlan(
        hook_style=_pick(hooks, digest[0]),
        corners=selected_corners,
        tsukkomi_density=_pick(densities, digest[1]),
        bridge_style=_pick(bridges, digest[2]),
    )


def variety_prompt_block(plan: VarietyPlan) -> str:
    """今回の抽選結果をLLMが実行できる具体的な指示へ変換する。"""
    corner_lines = (
        "\n".join(f"- {name}: {CORNER_STYLES[name]}" for name in plan.corners)
        if plan.corners
        else "- 挿入コーナーなし: 短尺のテンポを優先する。"
    )
    return (
        "\n\n【今回の演出バリエーション(必ず反映)】\n"
        f"- 導入フック: {plan.hook_style} — {HOOK_STYLES[plan.hook_style]}\n"
        f"- ツッコミ密度: {plan.tsukkomi_density} — "
        f"{TSUKKOMI_DENSITIES[plan.tsukkomi_density]}\n"
        f"- セクション間ブリッジ: {plan.bridge_style} — "
        f"{BRIDGE_STYLES[plan.bridge_style]}\n"
        "- 挿入コーナー:\n"
        f"{corner_lines}"
    )
