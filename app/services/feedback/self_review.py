"""投稿後の反応を振り返り、次回台本向けの改善Insightを作る。"""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.insight import Insight
from app.models.job_run import JobRun
from app.models.publication import Publication
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_metric_daily import VideoMetricDaily
from app.models.video_project import VideoProject
from app.providers.llm.base import LLMProvider
from app.schemas.self_review import SelfReviewReport
from app.services.feedback.insights import summarize_comment_categories
from app.services.jobs import run_idempotent_async
from app.services.llm_gateway import call_llm

logger = get_logger(__name__)

SELF_REVIEW_INSIGHT_TYPE = "self_review"
OPERATION = "self_review"
PROMPT_VERSION = "self_review_v1"
MODEL_POLICY = "mid"

_SYSTEM_PROMPT = (
    "あなたはYouTubeチャンネルの動画アナリストです。与えられた日次指標、"
    "場面別の視聴維持率分析、コメント分類から、動画の良かった点と悪かった点を分析し、"
    "次回の動画制作で実行すべき改善レッスンを最大5件抽出してください。"
    "recommended_actionは台本作家がそのまま実行できる具体的な指示文にしてください。"
    "指標から読み取れない因果関係を断定せず、入力データに基づいて記述してください。"
)


def _metric_for_review(
    session: Session, publication_id: str, metric_date: date | None
) -> VideoMetricDaily | None:
    query = session.query(VideoMetricDaily).filter(
        VideoMetricDaily.publication_id == publication_id
    )
    if metric_date is not None:
        return query.filter(VideoMetricDaily.metric_date == metric_date).one_or_none()
    return query.order_by(VideoMetricDaily.metric_date.desc()).first()


def build_self_review_idempotency_key(publication_id: str, metric: VideoMetricDaily) -> str:
    """投稿IDと最新指標日から日単位の冪等キーを作る。"""
    return f"self_review:{publication_id}:{metric.metric_date.isoformat()}"


def _retention_context(session: Session, publication_id: str) -> list[dict]:
    """既存の場面別維持率分析を、プロンプトへ渡せる小さな辞書へ整形する。"""
    rows = (
        session.query(Insight)
        .filter(
            Insight.source_type == "publication",
            Insight.source_id == publication_id,
            Insight.insight_type == "retention_scene_dip",
        )
        .order_by(Insight.created_at.desc())
        .limit(10)
        .all()
    )
    return [
        {
            "finding": row.finding,
            "recommended_action": row.recommended_action,
            "scene": row.evidence,
        }
        for row in rows
    ]


def _build_user_prompt(session: Session, publication: Publication, metric: VideoMetricDaily) -> str:
    project = session.get(VideoProject, publication.video_project_id)
    script = session.get(Script, project.script_id) if project and project.script_id else None
    sections = (script.body or {}).get("sections", []) if script else []
    payload = {
        "video": {
            "title": publication.title,
            "publication_id": publication.id,
            "script_sections": len(sections),
        },
        "latest_metric": {
            "metric_date": metric.metric_date.isoformat(),
            "views": metric.views,
            "watch_minutes": metric.watch_minutes,
            "average_view_duration_seconds": metric.average_view_duration,
            "average_view_percentage": metric.average_view_percentage,
            "impressions": metric.impressions,
            "ctr_ratio": metric.ctr,
            "likes": metric.likes,
            "comments": metric.comments_count,
            "subscribers_gained": metric.subscribers_gained,
            "subscribers_lost": metric.subscribers_lost,
        },
        "retention_scene_analysis": _retention_context(session, publication.id),
        "comment_category_counts": summarize_comment_categories(
            session, publication_id=publication.id
        ),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _insights_for_run(
    session: Session, *, publication_id: str, idempotency_key: str
) -> list[Insight]:
    refs = [f"{idempotency_key}:{index}" for index in range(5)]
    return (
        session.query(Insight)
        .filter(
            Insight.source_type == "publication",
            Insight.source_id == publication_id,
            Insight.insight_type == SELF_REVIEW_INSIGHT_TYPE,
            Insight.source_ref.in_(refs),
        )
        .order_by(Insight.source_ref.asc())
        .all()
    )


async def run_self_review(
    session: Session,
    *,
    publication_id: str,
    provider: LLMProvider,
    metric_date: date | None = None,
) -> list[Insight]:
    """投稿1件をLLMで振り返り、レッスンを指標日単位で冪等保存する。

    ``metric_date`` 未指定時は最新指標を使う。日次バッチは前日を明示指定できる。
    """
    publication = session.get(Publication, publication_id)
    if publication is None:
        raise ValueError(f"Publication not found: {publication_id}")

    metric = _metric_for_review(session, publication_id, metric_date)
    if metric is None:
        logger.info("self_review_skipped_no_metrics", publication_id=publication_id)
        return []

    idempotency_key = build_self_review_idempotency_key(publication_id, metric)

    async def _do_review(job_run: JobRun) -> list[Insight]:
        result = await call_llm(
            session,
            provider,
            operation=OPERATION,
            prompt_version=PROMPT_VERSION,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_build_user_prompt(session, publication, metric),
            response_schema=SelfReviewReport,
            model_policy=MODEL_POLICY,
            idempotency_key=idempotency_key,
            job_run_id=job_run.id,
        )
        report = SelfReviewReport.model_validate(result.data)
        created: list[Insight] = []
        for index, lesson in enumerate(report.lessons):
            insight = Insight(
                source_type="publication",
                source_id=publication_id,
                insight_type=SELF_REVIEW_INSIGHT_TYPE,
                source_ref=f"{idempotency_key}:{index}",
                finding=lesson.finding,
                evidence={
                    "metric_date": metric.metric_date.isoformat(),
                    "good_points": report.good_points,
                    "bad_points": report.bad_points,
                },
                confidence=0.6,
                recommended_action=lesson.recommended_action,
                human_review_reason="LLMによる自動振り返り(次回台本へ自動反映)",
            )
            session.add(insight)
            created.append(insight)
        session.flush()
        return created

    job_result = await run_idempotent_async(
        session,
        job_type=OPERATION,
        entity_type="publication",
        entity_id=publication_id,
        idempotency_key=idempotency_key,
        fn=_do_review,
    )
    if job_result.status != "succeeded":
        return _insights_for_run(
            session, publication_id=publication_id, idempotency_key=idempotency_key
        )
    return job_result.result or []


def collect_recent_lessons(session: Session, *, channel_id: str, limit: int = 5) -> list[Insight]:
    """チャンネル配下の直近セルフレビューレッスンを新しい順に返す。"""
    if limit <= 0:
        return []
    return (
        session.query(Insight)
        .join(Publication, Insight.source_id == Publication.id)
        .join(VideoProject, Publication.video_project_id == VideoProject.id)
        .join(Topic, VideoProject.topic_id == Topic.id)
        .filter(
            Topic.channel_id == channel_id,
            Insight.source_type == "publication",
            Insight.insight_type == SELF_REVIEW_INSIGHT_TYPE,
        )
        .order_by(Insight.created_at.desc(), Insight.id.desc())
        .limit(limit)
        .all()
    )
