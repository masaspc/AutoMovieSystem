"""台本の決定的ルールベース検査(仕様§10)。

`status` は本フェーズでは変更しない(draftのまま。reviewedへの遷移はPhase 4)。
検査結果はAPIのレスポンスに含めるのみ。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from app.models.script import Script

SEVERITY_BLOCKING = "blocking"
SEVERITY_WARNING = "warning"

# デフォルトの誇張・断定禁止表現(設定可能なNGワードリスト)。
DEFAULT_NG_WORDS: tuple[str, ...] = (
    "絶対に儲かる",
    "必ず儲かる",
    "絶対に上がる",
    "損しない",
    "必ず成功",
    "誰でも稼げる",
    "確実に稼げる",
)

# APIキー/シークレットらしきパターン。
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{12,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
]

# 重要な数値表現(%, 円, 倍)。
_IMPORTANT_NUMBER_PATTERN = re.compile(r"\d+(\.\d+)?\s*(%|円|倍|パーセント)")
_ANY_NUMBER_PATTERN = re.compile(r"\d")

_MAX_SENTENCE_LENGTH = 200
_SIMILARITY_THRESHOLD = 0.9


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str


def _split_sentences(text: str) -> list[str]:
    return [s for s in re.split(r"[。！？\n]", text) if s.strip()]


def _similarity_ratio(a: str | None, b: str | None) -> float:
    return SequenceMatcher(None, a or "", b or "").ratio()


def inspect_script(
    script: Script,
    *,
    previous_scripts: list[Script] | None = None,
    ng_words: tuple[str, ...] = DEFAULT_NG_WORDS,
) -> list[Finding]:
    """台本を決定的ルールベースで検査する。"""
    findings: list[Finding] = []
    body = script.body or {}

    title_candidates = body.get("title_candidates") or []
    sections = body.get("sections") or []

    # 公開・表示される全テキストを共通NGワードゲートへ通す。dialogueがある場合も
    # narrationを捨てず、両方を独立に検査してフィールド移動による回避を防ぐ。
    viewer_texts: list[tuple[str, str]] = [
        ("title", str(script.title or "")),
        ("script.hook", str(script.hook or "")),
        ("body.hook", str(body.get("hook") or "")),
        ("description", str(body.get("description") or "")),
        ("script.conclusion", str(script.conclusion or "")),
        ("body.conclusion", str(body.get("conclusion") or "")),
        ("script.call_to_action", str(script.call_to_action or "")),
        ("body.call_to_action", str(body.get("call_to_action") or "")),
    ]
    viewer_texts.extend(
        (f"title_candidates.{index}", str(candidate))
        for index, candidate in enumerate(title_candidates)
    )
    for section_index, section in enumerate(sections):
        viewer_texts.append(
            (f"sections.{section_index}.narration", str(section.get("narration") or ""))
        )
        viewer_texts.extend(
            (f"sections.{section_index}.dialogue.{dialogue_index}", str(item.get("text") or ""))
            for dialogue_index, item in enumerate(section.get("dialogue") or [])
        )

    for field_name, text in viewer_texts:
        for word in ng_words:
            if word in text:
                findings.append(
                    Finding(
                        "prohibited_expression",
                        SEVERITY_BLOCKING,
                        f"{field_name}に禁止表現「{word}」が含まれます",
                    )
                )

    if not title_candidates:
        findings.append(
            Finding("empty_title_candidates", SEVERITY_BLOCKING, "タイトル候補が空です")
        )
    if not sections:
        findings.append(Finding("empty_sections", SEVERITY_BLOCKING, "セクション(内容)が空です"))
    if not script.hook:
        findings.append(Finding("empty_hook", SEVERITY_BLOCKING, "hookが空です"))
    if not body.get("promised_outcome"):
        findings.append(
            Finding(
                "missing_promised_outcome",
                SEVERITY_BLOCKING,
                "視聴者利益(promised_outcome)の提示がありません",
            )
        )

    manifest_ids = set((script.source_manifest or {}).get("evidence_ids") or [])

    for idx, section in enumerate(sections):
        narration = section.get("narration") or ""
        dialogue_text = "\n".join(
            str(item.get("text") or "") for item in section.get("dialogue") or []
        )
        spoken_text = dialogue_text or narration
        evidence_ids = section.get("evidence_ids") or []

        for eid in evidence_ids:
            if eid not in manifest_ids:
                findings.append(
                    Finding(
                        "evidence_id_not_in_manifest",
                        SEVERITY_BLOCKING,
                        f"セクション{idx}のevidence_id({eid})がsource_manifestに存在しません",
                    )
                )

        has_important_number = bool(_IMPORTANT_NUMBER_PATTERN.search(spoken_text))
        has_any_number = bool(_ANY_NUMBER_PATTERN.search(spoken_text))
        if not evidence_ids and has_important_number:
            findings.append(
                Finding(
                    "unsupported_important_number",
                    SEVERITY_BLOCKING,
                    f"セクション{idx}に根拠のない重要な数値表現(%/円/倍)があります",
                )
            )
        elif not evidence_ids and has_any_number:
            findings.append(
                Finding(
                    "unsupported_number",
                    SEVERITY_WARNING,
                    f"セクション{idx}に根拠のない数値表現があります",
                )
            )

        for sentence in _split_sentences(spoken_text):
            if len(sentence) > _MAX_SENTENCE_LENGTH:
                findings.append(
                    Finding(
                        "sentence_too_long",
                        SEVERITY_WARNING,
                        f"セクション{idx}に読み上げ困難な200字超の文があります",
                    )
                )

        for pattern in _SECRET_PATTERNS:
            if pattern.search(spoken_text):
                findings.append(
                    Finding(
                        "possible_secret_leak",
                        SEVERITY_BLOCKING,
                        f"セクション{idx}にAPIキー/シークレットらしき文字列が含まれます",
                    )
                )

    if previous_scripts:
        for prev in previous_scripts:
            if prev.version == script.version:
                continue
            title_sim = _similarity_ratio(script.title, prev.title)
            hook_sim = _similarity_ratio(script.hook, prev.hook)
            if title_sim > _SIMILARITY_THRESHOLD and hook_sim > _SIMILARITY_THRESHOLD:
                findings.append(
                    Finding(
                        "similar_to_previous_version",
                        SEVERITY_WARNING,
                        f"version {prev.version} とtitle/hookが高い類似度"
                        f"({title_sim:.2f}/{hook_sim:.2f})です",
                    )
                )

    return findings


def inspect_script_with_history(
    session: Session, script: Script, *, ng_words: tuple[str, ...] = DEFAULT_NG_WORDS
) -> list[Finding]:
    """DBから同一Topicの過去バージョンを取得したうえで検査する(API/ジョブ用ヘルパー)。"""
    previous = (
        session.query(Script)
        .filter(Script.topic_id == script.topic_id, Script.version != script.version)
        .all()
    )
    return inspect_script(script, previous_scripts=previous, ng_words=ng_words)
