"""Topic のスコアリング(仕様§8)。

重みは `app/core/config.py` の `TOPIC_SCORE_WEIGHT_*` から取得する。合計が1.0で
ない場合はスコアリング前に検証エラーとする(丸め誤差を考慮した許容誤差付き)。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.topic import Topic
from app.services.jobs import run_idempotent

_WEIGHT_SUM_TOLERANCE = 1e-6


@dataclass(frozen=True)
class ScoreWeights:
    """§8の6軸の重み。"""

    demand: float
    expertise: float
    originality: float
    revenue: float
    freshness: float
    production_cost: float

    def total(self) -> float:
        return (
            self.demand
            + self.expertise
            + self.originality
            + self.revenue
            + self.freshness
            + self.production_cost
        )


class InvalidWeightsError(ValueError):
    """重みの合計が1.0でない場合。"""


class TopicNotFoundError(ValueError):
    """指定されたTopicが存在しない場合。"""


def weights_from_settings(settings: Settings | None = None) -> ScoreWeights:
    """アプリ設定からデフォルトの重みを組み立てる。"""
    settings = settings or get_settings()
    return ScoreWeights(
        demand=settings.TOPIC_SCORE_WEIGHT_DEMAND,
        expertise=settings.TOPIC_SCORE_WEIGHT_EXPERTISE,
        originality=settings.TOPIC_SCORE_WEIGHT_ORIGINALITY,
        revenue=settings.TOPIC_SCORE_WEIGHT_REVENUE,
        freshness=settings.TOPIC_SCORE_WEIGHT_FRESHNESS,
        production_cost=settings.TOPIC_SCORE_WEIGHT_PRODUCTION_COST,
    )


def validate_weights(weights: ScoreWeights) -> None:
    """重みの合計が1.0でなければ `InvalidWeightsError` を送出する。"""
    total = weights.total()
    if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
        raise InvalidWeightsError(f"重みの合計は1.0である必要があります(実際: {total})")


def compute_total_score(topic: Topic, weights: ScoreWeights) -> float:
    """各軸スコア(0-10)と重みから total_score を算出する。"""
    validate_weights(weights)
    return (
        topic.demand_score * weights.demand
        + topic.expertise_score * weights.expertise
        + topic.originality_score * weights.originality
        + topic.revenue_score * weights.revenue
        + topic.freshness_score * weights.freshness
        + topic.production_cost_score * weights.production_cost
    )


def score_topic(
    session: Session,
    topic_id: str,
    *,
    weights: ScoreWeights | None = None,
    trace_id: str | None = None,
) -> Topic:
    """Topic.total_score を計算し、status を created -> scored へ進める(冪等)。

    JobRun(job_type="score_topic")経由で実行するため、同一Topicへの
    再実行(idempotency_key固定)は初回成功後は何もしない。
    """
    weights = weights or weights_from_settings()
    validate_weights(weights)

    topic = session.get(Topic, topic_id)
    if topic is None:
        raise TopicNotFoundError(f"Topic not found: {topic_id}")

    def _do_score() -> float:
        total = compute_total_score(topic, weights)
        topic.total_score = total
        if topic.status == "created":
            topic.status = "scored"
        session.flush()
        return total

    run_idempotent(
        session,
        job_type="score_topic",
        entity_type="topic",
        entity_id=topic.id,
        idempotency_key=f"score_topic:{topic.id}",
        fn=_do_score,
        trace_id=trace_id,
    )
    return topic
