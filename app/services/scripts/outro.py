"""エンディング(チャンネル登録CTA)セクションの自動付与。

チャンネル登録・グッドボタンの呼びかけは全動画に必ず入れる運用要件のため、
LLMの出力揺れに任せず、台本生成の最終段でプログラムが決定的に付与する。
セリフはずんだもん×つむぎの掛け合い(つむぎがキャスト外の場合はめたん)で、
topic_idベースの決定的選択により同一動画は常に同じバリエーションになる(冪等)。
"""

from __future__ import annotations

import hashlib

from app.schemas.script_content import (
    CharacterId,
    ScriptContent,
    ScriptDialogueLine,
    ScriptSection,
    VisualType,
)

OUTRO_HEADING = "エンディング"
OUTRO_VISUAL_TYPE: VisualType = "cta"

# (zundamonのセリフ, 相方のセリフ, zundamonの締め) のバリエーション。
# 相方はつむぎ優先(キャスト外ならめたん)。誇張・断定は使わない(絶対原則)。
_OUTRO_VARIATIONS: tuple[tuple[str, str, str], ...] = (
    (
        "今日の内容が役に立ったら、グッドボタンをおしてほしいのだ!",
        "チャンネル登録と通知ベルもお願いします。次の動画を見逃さずにすみますよ。",
        "コメントで感想や質問も待ってるのだ。次の動画でまた会うのだ〜!",
    ),
    (
        "ここまで見てくれてありがとうなのだ!よかったらグッドボタンをおしてなのだ!",
        "チャンネル登録していただけると、続きのエピソードをすぐ見つけられますよ。",
        "それじゃあ、次回もいっしょに勉強するのだ〜!",
    ),
    (
        "少しでも「わかった!」と思ったら、グッドボタンで教えてほしいのだ!",
        "チャンネル登録は無料です。応援していただけるととてもうれしいです。",
        "質問はコメント欄で待ってるのだ。次の動画でまた会うのだ〜!",
    ),
)


def _pick_variation(seed: str) -> tuple[str, str, str]:
    digest = hashlib.sha256(f"outro:{seed}".encode()).digest()
    return _OUTRO_VARIATIONS[digest[0] % len(_OUTRO_VARIATIONS)]


def build_outro_section(*, seed: str, cast: list[str]) -> ScriptSection:
    """チャンネル登録CTAのエンディングセクションを構築する。"""
    zundamon_line, partner_line, closing_line = _pick_variation(seed)
    partner: CharacterId = "tsumugi" if "tsumugi" in cast else "metan"
    dialogue = [
        ScriptDialogueLine(speaker="zundamon", text=zundamon_line, emotion="happy"),
        ScriptDialogueLine(speaker=partner, text=partner_line, emotion="happy"),
        ScriptDialogueLine(speaker="zundamon", text=closing_line, emotion="happy"),
    ]
    narration = " ".join(line.text for line in dialogue)
    return ScriptSection(
        heading=OUTRO_HEADING,
        narration=narration,
        visual_instruction="グッドボタンとチャンネル登録ボタンを大きく表示するエンディング画面",
        visual_type=OUTRO_VISUAL_TYPE,
        background_style="card",
        character_layout="full",
        visual_title="最後までありがとうございました!",
        emphasis_words=["チャンネル登録", "グッドボタン"],
        dialogue=dialogue,
    )


def append_outro_section(content: ScriptContent, *, seed: str, cast: list[str]) -> ScriptContent:
    """台本末尾へエンディングCTAセクションを付与する(既にあれば何もしない=冪等)。"""
    if content.sections and content.sections[-1].visual_type == OUTRO_VISUAL_TYPE:
        return content
    content.sections.append(build_outro_section(seed=seed, cast=cast))
    return content
