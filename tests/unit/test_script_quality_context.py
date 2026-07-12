from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.insight import Insight
from app.models.publication import Publication
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.services.scripts.quality_context import (
    build_learning_story_context,
    build_retention_feedback_context,
)


def test_learning_story_context_requires_prediction_failure_fix_and_transfer() -> None:
    context = build_learning_story_context()
    assert "予想" in context
    assert "失敗例" in context
    assert "修正前後" in context
    assert "確認問題" in context
    assert "30秒以内で試せる課題" in context


def test_retention_feedback_is_aggregated_by_visual_type(db_session: Session) -> None:
    channel = Channel(name="quality-channel")
    db_session.add(channel)
    db_session.flush()
    target = Topic(
        channel_id=channel.id,
        title="次の動画",
        source_type="manual",
        source_ref="next-video",
    )
    source = Topic(
        channel_id=channel.id,
        title="過去動画",
        source_type="manual",
        source_ref="past-video",
    )
    db_session.add_all([target, source])
    db_session.flush()
    project = VideoProject(topic_id=source.id)
    db_session.add(project)
    db_session.flush()
    publication = Publication(
        video_project_id=project.id,
        title="過去動画",
        description="",
        idempotency_key="quality-context-publication",
        upload_status="completed",
    )
    db_session.add(publication)
    db_session.flush()
    for section_index in range(2):
        db_session.add(
            Insight(
                source_type="publication",
                source_id=publication.id,
                insight_type="retention_scene_dip",
                source_ref=f"retention:{publication.id}:section:{section_index}",
                finding="離脱",
                evidence={"visual_type": "code", "section_index": section_index},
                confidence=0.8,
                recommended_action="改善",
                human_review_reason="確認",
            )
        )
    db_session.flush()

    context = build_retention_feedback_context(db_session, target.id)

    assert "code: 離脱場面2件" in context
    assert "予想→実行行の強調→出力→修正" in context


def test_single_retention_dip_is_not_overfitted(db_session: Session) -> None:
    channel = Channel(name="single-dip-channel")
    db_session.add(channel)
    db_session.flush()
    target = Topic(
        channel_id=channel.id, title="次", source_type="manual", source_ref="single-next"
    )
    source = Topic(
        channel_id=channel.id, title="前", source_type="manual", source_ref="single-past"
    )
    db_session.add_all([target, source])
    db_session.flush()
    project = VideoProject(topic_id=source.id)
    db_session.add(project)
    db_session.flush()
    publication = Publication(
        video_project_id=project.id,
        title="前",
        description="",
        idempotency_key="single-dip-publication",
        upload_status="completed",
    )
    db_session.add(publication)
    db_session.flush()
    db_session.add(
        Insight(
            source_type="publication",
            source_id=publication.id,
            insight_type="retention_scene_dip",
            source_ref="single-dip",
            finding="離脱",
            evidence={"visual_type": "quiz"},
            confidence=0.8,
            recommended_action="改善",
            human_review_reason="確認",
        )
    )
    db_session.flush()

    assert build_retention_feedback_context(db_session, target.id) == ""
