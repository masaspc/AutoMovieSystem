"""コメント分類の決定的ルール実装(Phase 6 MVP)。"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.comment import COMMENT_CATEGORIES, Comment


@dataclass(frozen=True)
class CommentClassification:
    category: str
    sentiment: str
    priority: int
    requested_topic: str | None
    requires_response: bool


def classify_text(text: str) -> CommentClassification:
    """MVP用の低コストなルール分類。実LLM分類は後続フェーズで差し替え可能。"""
    normalized = text.strip()
    lower = normalized.lower()

    if any(word in normalized for word in ("至急", "緊急", "すぐ")):
        return CommentClassification("URGENT", "neutral", 90, None, True)
    if any(word in lower for word in ("spam", "http://", "https://")) and "youtube" not in lower:
        return CommentClassification("SPAM", "negative", 0, None, False)
    if any(word in normalized for word in ("著作権", "権利", "削除依頼")):
        return CommentClassification("RIGHTS_REQUEST", "neutral", 80, None, True)
    if any(word in normalized for word in ("次回", "取り上げ", "動画にして", "解説して")):
        return CommentClassification("NEXT_TOPIC_REQUEST", "neutral", 60, normalized[:255], True)
    if "比較" in normalized:
        return CommentClassification("COMPARISON_REQUEST", "neutral", 50, normalized[:255], True)
    if any(word in normalized for word in ("エラー", "失敗", "動かない", "不具合")):
        return CommentClassification("PROBLEM_REPORT", "negative", 70, None, True)
    if any(word in normalized for word in ("訂正", "間違", "誤り")):
        return CommentClassification("CORRECTION", "negative", 70, None, True)
    if "?" in normalized or "？" in normalized:
        return CommentClassification("QUESTION", "neutral", 40, None, True)
    if any(word in normalized for word in ("ありがとう", "助か", "良い", "最高")):
        return CommentClassification("POSITIVE", "positive", 10, None, False)
    if any(word in normalized for word in ("悪い", "微妙", "嫌い", "わかりにくい")):
        return CommentClassification("NEGATIVE", "negative", 30, None, True)

    return CommentClassification("OTHER", "neutral", 0, None, False)


def classify_comment(comment: Comment) -> Comment:
    classification = classify_text(comment.text)
    if classification.category not in COMMENT_CATEGORIES:  # pragma: no cover - 防御的検証
        raise ValueError(f"Unknown comment category: {classification.category}")
    comment.category = classification.category
    comment.sentiment = classification.sentiment
    comment.priority = classification.priority
    comment.requested_topic = classification.requested_topic
    comment.requires_response = classification.requires_response
    return comment
