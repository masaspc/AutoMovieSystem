"""コメント・指標からのInsight生成(Phase 6)。"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.comment import Comment
from app.models.insight import Insight
from app.models.publication import Publication
from app.models.topic import Topic
from app.models.video_metric_daily import VideoMetricDaily
from app.models.video_project import VideoProject


class PublicationNotFoundError(ValueError):
    """Publication が存在しない場合。"""


@dataclass(frozen=True)
class FeedbackRuleConfig:
    same_question_threshold: int = 3
    problem_report_threshold: int = 2
    next_topic_threshold: int = 3
    comparison_request_threshold: int = 5
    low_ctr_threshold: float = 0.03
    high_ctr_threshold: float = 0.08
    high_retention_threshold: float = 0.50
    low_retention_threshold: float = 0.30
    high_subscriber_gain_rate: float = 0.02
    high_comment_rate: float = 0.03
    low_view_threshold: int = 1000


DEFAULT_RULE_CONFIG = FeedbackRuleConfig()


def _get_publication(session: Session, publication_id: str) -> Publication:
    publication = session.get(Publication, publication_id)
    if publication is None:
        raise PublicationNotFoundError(f"Publication not found: {publication_id}")
    return publication


def _stable_ref(*parts: str) -> str:
    digest = hashlib.sha256(":".join(parts).encode()).hexdigest()
    return digest[:48]


def _comment_group_key(comment: Comment) -> str:
    return (comment.requested_topic or comment.text).strip()[:255]


def _topic_source_ref(publication_id: str, insight_type: str, group_key: str) -> str:
    return f"comment:{insight_type}:{_stable_ref(publication_id, insight_type, group_key)}"


def _create_or_get_topic_from_request(
    session: Session,
    publication: Publication,
    *,
    title: str,
    description: str,
    source_ref: str,
) -> Topic | None:
    project = session.get(VideoProject, publication.video_project_id)
    if project is None:
        return None
    source_topic = session.get(Topic, project.topic_id)
    if source_topic is None:
        return None
    existing = (
        session.query(Topic)
        .filter(
            Topic.channel_id == source_topic.channel_id,
            Topic.source_type == "comment",
            Topic.source_ref == source_ref,
        )
        .one_or_none()
    )
    if existing is not None:
        return existing

    topic = Topic(
        channel_id=source_topic.channel_id,
        title=title[:255],
        description=description,
        source_type="comment",
        source_ref=source_ref,
    )
    try:
        with session.begin_nested():
            session.add(topic)
            session.flush()
    except IntegrityError:
        session.expunge(topic)
        return (
            session.query(Topic)
            .filter(
                Topic.channel_id == source_topic.channel_id,
                Topic.source_type == "comment",
                Topic.source_ref == source_ref,
            )
            .one_or_none()
        )
    return topic


def _upsert_insight(
    session: Session,
    *,
    source_type: str,
    source_id: str,
    insight_type: str,
    source_ref: str,
    finding: str,
    evidence: dict,
    confidence: float,
    recommended_action: str,
    human_review_reason: str,
) -> Insight:
    existing = (
        session.query(Insight)
        .filter(
            Insight.source_type == source_type,
            Insight.source_id == source_id,
            Insight.insight_type == insight_type,
            Insight.source_ref == source_ref,
        )
        .one_or_none()
    )
    if existing is not None:
        insight = existing
        insight.finding = finding
        insight.evidence = evidence
        insight.confidence = confidence
        insight.recommended_action = recommended_action
        insight.human_review_reason = human_review_reason
        return insight

    insight = Insight(
        source_type=source_type,
        source_id=source_id,
        insight_type=insight_type,
        source_ref=source_ref,
        finding=finding,
        evidence=evidence,
        confidence=confidence,
        recommended_action=recommended_action,
        human_review_reason=human_review_reason,
    )
    session.add(insight)
    return insight


def _comment_ids(comments: list[Comment]) -> list[str]:
    return [comment.id for comment in comments]


def _generate_grouped_comment_insights(
    session: Session,
    *,
    publication: Publication,
    comments: list[Comment],
    category: str,
    threshold: int,
    insight_type: str,
    finding_template: str,
    recommended_action: str,
    human_review_reason: str,
    creates_topic: bool,
) -> list[Insight]:
    grouped: dict[str, list[Comment]] = {}
    for comment in comments:
        if comment.category != category or comment.moderation_status == "deleted":
            continue
        grouped.setdefault(_comment_group_key(comment), []).append(comment)

    insights: list[Insight] = []
    for group_key, group_comments in grouped.items():
        count = len(group_comments)
        if count < threshold:
            continue
        source_ref = _topic_source_ref(publication.id, insight_type, group_key)
        topic: Topic | None = None
        if creates_topic:
            topic = _create_or_get_topic_from_request(
                session,
                publication,
                title=group_key,
                description=f"コメント由来の候補: {group_key}",
                source_ref=source_ref,
            )
        evidence = {
            "rule": insight_type,
            "threshold": threshold,
            "count": count,
            "category": category,
            "group_key": group_key,
            "comment_ids": _comment_ids(group_comments),
            "topic_id": topic.id if topic is not None else None,
        }
        insights.append(
            _upsert_insight(
                session,
                source_type="publication",
                source_id=publication.id,
                insight_type=insight_type,
                source_ref=source_ref,
                finding=finding_template.format(count=count, group_key=group_key),
                evidence=evidence,
                confidence=min(1.0, count / max(threshold, 1)),
                recommended_action=recommended_action,
                human_review_reason=human_review_reason,
            )
        )
    return insights


def generate_comment_insights(
    session: Session,
    *,
    publication_id: str,
    config: FeedbackRuleConfig = DEFAULT_RULE_CONFIG,
    next_topic_threshold: int | None = None,
) -> list[Insight]:
    """コメント分類結果からInsightを作る。"""
    publication = _get_publication(session, publication_id)
    if next_topic_threshold is not None:
        config = FeedbackRuleConfig(
            same_question_threshold=config.same_question_threshold,
            problem_report_threshold=config.problem_report_threshold,
            next_topic_threshold=next_topic_threshold,
            comparison_request_threshold=config.comparison_request_threshold,
            low_ctr_threshold=config.low_ctr_threshold,
            high_ctr_threshold=config.high_ctr_threshold,
            high_retention_threshold=config.high_retention_threshold,
            low_retention_threshold=config.low_retention_threshold,
            high_subscriber_gain_rate=config.high_subscriber_gain_rate,
            high_comment_rate=config.high_comment_rate,
            low_view_threshold=config.low_view_threshold,
        )
    comments = session.query(Comment).filter(Comment.publication_id == publication_id).all()

    insights: list[Insight] = []
    insights.extend(
        _generate_grouped_comment_insights(
            session,
            publication=publication,
            comments=comments,
            category="NEXT_TOPIC_REQUEST",
            threshold=config.next_topic_threshold,
            insight_type="next_topic_request",
            finding_template="同種の次回企画要望が{count}件あります: {group_key}",
            recommended_action="次回企画候補としてレビューする",
            human_review_reason="視聴者要望に基づくため、チャンネル方針と重複企画の確認が必要",
            creates_topic=True,
        )
    )
    insights.extend(
        _generate_grouped_comment_insights(
            session,
            publication=publication,
            comments=comments,
            category="QUESTION",
            threshold=config.same_question_threshold,
            insight_type="same_question_topic",
            finding_template="同種質問が{count}件あります: {group_key}",
            recommended_action="FAQまたは解説企画候補としてレビューする",
            human_review_reason="質問の意図を人間が確認し、既存動画で回答済みか判断する必要がある",
            creates_topic=True,
        )
    )
    insights.extend(
        _generate_grouped_comment_insights(
            session,
            publication=publication,
            comments=comments,
            category="PROBLEM_REPORT",
            threshold=config.problem_report_threshold,
            insight_type="problem_supplement",
            finding_template="同一エラー/不具合報告が{count}件あります: {group_key}",
            recommended_action="補足説明・固定コメント・追加動画の必要性を確認する",
            human_review_reason="不具合の再現性と責任範囲を人間が確認する必要がある",
            creates_topic=False,
        )
    )
    insights.extend(
        _generate_grouped_comment_insights(
            session,
            publication=publication,
            comments=comments,
            category="COMPARISON_REQUEST",
            threshold=config.comparison_request_threshold,
            insight_type="comparison_video_request",
            finding_template="比較要望が{count}件あります: {group_key}",
            recommended_action="比較動画候補としてレビューする",
            human_review_reason="比較対象の公平性・最新性・権利表現を人間が確認する必要がある",
            creates_topic=True,
        )
    )

    session.flush()
    return insights


def _latest_metric(session: Session, publication_id: str) -> VideoMetricDaily | None:
    return (
        session.query(VideoMetricDaily)
        .filter(VideoMetricDaily.publication_id == publication_id)
        .order_by(VideoMetricDaily.metric_date.desc())
        .first()
    )


def generate_metric_insights(
    session: Session,
    *,
    publication_id: str,
    config: FeedbackRuleConfig = DEFAULT_RULE_CONFIG,
) -> list[Insight]:
    """日次指標から改善Insightを作る。"""
    _get_publication(session, publication_id)
    metric = _latest_metric(session, publication_id)
    if metric is None:
        return []

    insights: list[Insight] = []
    metric_ref = f"metric:{metric.id}"
    common_evidence = {
        "metric_id": metric.id,
        "metric_date": metric.metric_date.isoformat(),
        "views": metric.views,
        "ctr": metric.ctr,
        "average_view_percentage": metric.average_view_percentage,
        "comments_count": metric.comments_count,
        "subscribers_gained": metric.subscribers_gained,
    }

    if metric.ctr <= config.low_ctr_threshold and (
        metric.average_view_percentage >= config.high_retention_threshold
    ):
        insights.append(
            _upsert_insight(
                session,
                source_type="publication",
                source_id=publication_id,
                insight_type="title_thumbnail_improvement",
                source_ref=f"{metric_ref}:low_ctr_high_retention",
                finding="CTRは低い一方で視聴維持率が高いため、タイトル・サムネイル改善余地があります",
                evidence={
                    **common_evidence,
                    "rule": "low_ctr_high_retention",
                    "low_ctr_threshold": config.low_ctr_threshold,
                    "high_retention_threshold": config.high_retention_threshold,
                },
                confidence=0.75,
                recommended_action="タイトル・サムネイルの改善案を作成してレビューする",
                human_review_reason="クリック誘導と内容の整合性を人間が確認する必要がある",
            )
        )

    if metric.ctr >= config.high_ctr_threshold and (
        metric.average_view_percentage <= config.low_retention_threshold
    ):
        insights.append(
            _upsert_insight(
                session,
                source_type="publication",
                source_id=publication_id,
                insight_type="content_mismatch",
                source_ref=f"{metric_ref}:high_ctr_low_retention",
                finding="CTRは高い一方で冒頭維持率が低いため、期待内容との不一致が疑われます",
                evidence={
                    **common_evidence,
                    "rule": "high_ctr_low_retention",
                    "high_ctr_threshold": config.high_ctr_threshold,
                    "low_retention_threshold": config.low_retention_threshold,
                },
                confidence=0.7,
                recommended_action="冒頭構成とタイトル・サムネイルの約束を見直す",
                human_review_reason="動画内容と訴求のズレは定性的確認が必要",
            )
        )

    if metric.views > 0 and metric.subscribers_gained / metric.views >= (
        config.high_subscriber_gain_rate
    ):
        insights.append(
            _upsert_insight(
                session,
                source_type="publication",
                source_id=publication_id,
                insight_type="focus_theme",
                source_ref=f"{metric_ref}:subscriber_gain_rate",
                finding="再生数に対して登録増が高く、重点テーマ候補です",
                evidence={
                    **common_evidence,
                    "rule": "high_subscriber_gain_rate",
                    "subscriber_gain_rate": metric.subscribers_gained / metric.views,
                    "threshold": config.high_subscriber_gain_rate,
                },
                confidence=0.7,
                recommended_action="同テーマの続編・シリーズ化を検討する",
                human_review_reason="一時的要因かチャンネル戦略に合うテーマか確認が必要",
            )
        )

    if metric.views > 0 and metric.views <= config.low_view_threshold:
        comment_rate = metric.comments_count / metric.views
        if comment_rate >= config.high_comment_rate:
            insights.append(
                _upsert_insight(
                    session,
                    source_type="publication",
                    source_id=publication_id,
                    insight_type="specialist_series_candidate",
                    source_ref=f"{metric_ref}:comment_rate_low_views",
                    finding="再生数は少ない一方でコメント率が高く、専門シリーズ候補です",
                    evidence={
                        **common_evidence,
                        "rule": "high_comment_rate_low_views",
                        "comment_rate": comment_rate,
                        "high_comment_rate": config.high_comment_rate,
                        "low_view_threshold": config.low_view_threshold,
                    },
                    confidence=0.65,
                    recommended_action="専門性の高い続編または補足企画を検討する",
                    human_review_reason="少数の濃い需要か偶発的な反応か人間が確認する必要がある",
                )
            )

    session.flush()
    return insights


def generate_publication_insights(
    session: Session,
    *,
    publication_id: str,
    config: FeedbackRuleConfig = DEFAULT_RULE_CONFIG,
) -> list[Insight]:
    """コメントと指標の両方からInsightを生成する。"""
    insights = generate_comment_insights(session, publication_id=publication_id, config=config)
    existing_ids = {insight.id for insight in insights if insight.id is not None}
    for insight in generate_metric_insights(session, publication_id=publication_id, config=config):
        if insight.id not in existing_ids:
            insights.append(insight)
    return insights


def summarize_comment_categories(session: Session, *, publication_id: str) -> dict[str, int]:
    """API向けのカテゴリ別件数を返す。"""
    comments = (
        session.query(Comment.category)
        .filter(Comment.publication_id == publication_id, Comment.moderation_status != "deleted")
        .all()
    )
    return dict(Counter(category for (category,) in comments))
