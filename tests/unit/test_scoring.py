from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.topic import Topic
from app.services.topics.scoring import (
    InvalidWeightsError,
    ScoreWeights,
    TopicNotFoundError,
    compute_total_score,
    score_topic,
    validate_weights,
    weights_from_settings,
)


def _make_channel(db_session: Session) -> Channel:
    channel = Channel(name="tech-channel")
    db_session.add(channel)
    db_session.flush()
    return channel


def _make_topic(db_session: Session, channel: Channel, **scores: float) -> Topic:
    topic = Topic(
        channel_id=channel.id,
        title="サンプル企画",
        source_type="manual",
        source_ref="key-1",
        **scores,
    )
    db_session.add(topic)
    db_session.flush()
    return topic


def test_weights_from_settings_sum_to_one() -> None:
    weights = weights_from_settings()
    validate_weights(weights)  # 例外が出なければOK
    assert weights.total() == pytest.approx(1.0)


def test_validate_weights_rejects_non_unit_sum() -> None:
    weights = ScoreWeights(
        demand=0.5,
        expertise=0.5,
        originality=0.5,
        revenue=0.0,
        freshness=0.0,
        production_cost=0.0,
    )
    with pytest.raises(InvalidWeightsError):
        validate_weights(weights)


def test_compute_total_score_weighted_average(db_session: Session) -> None:
    channel = _make_channel(db_session)
    topic = _make_topic(
        db_session,
        channel,
        demand_score=10.0,
        expertise_score=10.0,
        originality_score=10.0,
        revenue_score=10.0,
        freshness_score=10.0,
        production_cost_score=10.0,
    )
    weights = weights_from_settings()
    assert compute_total_score(topic, weights) == pytest.approx(10.0)


def test_compute_total_score_mixed_values(db_session: Session) -> None:
    channel = _make_channel(db_session)
    topic = _make_topic(
        db_session,
        channel,
        demand_score=8.0,
        expertise_score=6.0,
        originality_score=4.0,
        revenue_score=2.0,
        freshness_score=0.0,
        production_cost_score=10.0,
    )
    weights = ScoreWeights(
        demand=0.25,
        expertise=0.20,
        originality=0.20,
        revenue=0.15,
        freshness=0.10,
        production_cost=0.10,
    )
    expected = 8.0 * 0.25 + 6.0 * 0.20 + 4.0 * 0.20 + 2.0 * 0.15 + 0.0 * 0.10 + 10.0 * 0.10
    assert compute_total_score(topic, weights) == pytest.approx(expected)


def test_score_topic_updates_status_and_total_score(db_session: Session) -> None:
    channel = _make_channel(db_session)
    topic = _make_topic(
        db_session,
        channel,
        demand_score=10.0,
        expertise_score=10.0,
        originality_score=10.0,
        revenue_score=10.0,
        freshness_score=10.0,
        production_cost_score=10.0,
    )
    assert topic.status == "created"

    updated = score_topic(db_session, topic.id)

    assert updated.status == "scored"
    assert updated.total_score == pytest.approx(10.0)


def test_score_topic_not_found_raises(db_session: Session) -> None:
    with pytest.raises(TopicNotFoundError):
        score_topic(db_session, "does-not-exist")


def test_score_topic_is_idempotent(db_session: Session) -> None:
    channel = _make_channel(db_session)
    topic = _make_topic(
        db_session,
        channel,
        demand_score=10.0,
        expertise_score=10.0,
        originality_score=10.0,
        revenue_score=10.0,
        freshness_score=10.0,
        production_cost_score=10.0,
    )
    score_topic(db_session, topic.id)
    topic.demand_score = 0.0  # 再スコアされていれば下がるはず
    db_session.flush()

    result = score_topic(db_session, topic.id)

    # 既にJobRunがsucceededのため再計算されず、total_scoreは変わらない。
    assert result.total_score == pytest.approx(10.0)
