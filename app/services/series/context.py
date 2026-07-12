"""Episodeに紐づくTopicの台本生成へシリーズ全体の制約を渡す。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.episode_plan import EpisodePlan
from app.models.series_plan import SeriesPlan
from app.services.scripts.quality_context import build_quality_context


def build_series_script_context(session: Session, topic_id: str) -> str:
    quality_context = build_quality_context(session, topic_id)
    episode = session.query(EpisodePlan).filter(EpisodePlan.topic_id == topic_id).one_or_none()
    if episode is None:
        return quality_context
    series = session.get(SeriesPlan, episode.series_plan_id)
    if series is None:
        return quality_context
    previous = (
        session.query(EpisodePlan)
        .filter(
            EpisodePlan.series_plan_id == series.id,
            EpisodePlan.position < episode.position,
        )
        .order_by(EpisodePlan.position)
        .all()
    )
    previous_lines = [
        f"- 第{item.position}回 {item.title}: 説明済み={', '.join(item.new_concepts)}"
        for item in previous
    ]
    return quality_context + (
        "\n\n[シリーズ制作上の必須制約]\n"
        f"シリーズ: {series.name}（全{series.planned_episode_count}回）\n"
        f"今回: 第{episode.position}回 {episode.title}\n"
        f"対象視聴者: {series.target_audience}\n開始時の知識: {series.starting_knowledge}\n"
        f"最終到達目標: {series.final_goal}\n全体方針: {series.series_prompt}\n"
        f"共通ルール: {series.shared_rules}\n技術・環境: {series.technology_version} / "
        f"{series.development_environment}\n"
        f"今回の学習目標: {', '.join(episode.learning_objectives)}\n"
        f"今回初めて説明する概念: {', '.join(episode.new_concepts)}\n"
        f"復習可能な概念: {', '.join(episode.review_concepts)}\n"
        f"まだ説明・使用してはいけない概念: {', '.join(episode.excluded_concepts)}\n"
        f"デモ: {episode.demo_outline}\n演習: {episode.exercise_outline}\n"
        f"次回への接続: {episode.next_episode_bridge}\n"
        "過去回:\n"
        f"{chr(10).join(previous_lines) or '- なし'}\n"
        "未説明概念をサンプルコードにも使用せず、過去回と同じ説明の重複を避けてください。"
    )
