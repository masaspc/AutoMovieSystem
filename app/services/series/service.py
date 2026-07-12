"""シリーズ全体のカリキュラム生成・承認・EpisodeからTopic作成。"""

from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from app.models.episode_plan import EpisodePlan
from app.models.job_run import JobRun
from app.models.series_plan import SeriesPlan
from app.models.topic import Topic
from app.providers.llm.base import LLMProvider
from app.schemas.production_settings import ProductionSettings
from app.schemas.series import CurriculumPlan
from app.services.jobs import JobInProgressError, run_idempotent_async
from app.services.llm_gateway import call_llm
from app.services.orchestration import _get_or_create_video_project

PROMPT_VERSION = "series_curriculum_v1"


def _series_fingerprint(series: SeriesPlan) -> str:
    value = "|".join(
        (
            series.name,
            series.target_audience,
            series.starting_knowledge,
            series.final_goal,
            series.series_prompt,
            series.shared_rules,
            series.technology_version,
            series.development_environment,
            str(series.planned_episode_count),
            str(series.curriculum_version),
        )
    )
    return hashlib.sha256(value.encode()).hexdigest()


async def generate_curriculum(
    session: Session, *, series_id: str, provider: LLMProvider
) -> list[EpisodePlan]:
    series = session.get(SeriesPlan, series_id)
    if series is None:
        raise ValueError("SeriesPlan not found")
    existing = (
        session.query(EpisodePlan)
        .filter(EpisodePlan.series_plan_id == series.id)
        .order_by(EpisodePlan.position)
        .all()
    )
    if any(episode.topic_id for episode in existing):
        raise ValueError("制作開始済みのEpisodeがあるためカリキュラムを再生成できません")
    idempotency_key = f"generate_curriculum:{series.id}:{_series_fingerprint(series)[:20]}"

    async def _do_generate(job_run: JobRun) -> list[EpisodePlan]:
        result = await call_llm(
            session,
            provider,
            operation="generate_curriculum",
            prompt_version=PROMPT_VERSION,
            system_prompt=(
                "あなたは初心者向け動画講座のカリキュラム設計者です。全話の依存関係を整理し、"
                "未説明概念を先に使わず、重複と難易度の急上昇を避けてください。"
            ),
            user_prompt=(
                f"シリーズ名: {series.name}\n対象: {series.target_audience}\n"
                f"開始時の知識: {series.starting_knowledge}\n最終目標: {series.final_goal}\n"
                f"全体方針: {series.series_prompt}\n共通ルール: {series.shared_rules}\n"
                f"技術: {series.technology_version}\n環境: {series.development_environment}\n"
                f"episode_count={series.planned_episode_count}\n"
                "各話に学習目標、新規概念、復習概念、まだ扱わない概念、デモ、演習、次回接続を設定してください。"
            ),
            response_schema=CurriculumPlan,
            model_policy="high",
            idempotency_key=idempotency_key,
            job_run_id=job_run.id,
        )
        curriculum = CurriculumPlan.model_validate(result.data)
        if len(curriculum.episodes) != series.planned_episode_count:
            raise ValueError("生成されたEpisode数が計画数と一致しません")
        positions = [episode.position for episode in curriculum.episodes]
        if sorted(positions) != list(range(1, series.planned_episode_count + 1)):
            raise ValueError("Episodeのpositionが1からの連番ではありません")
        for episode in existing:
            session.delete(episode)
        session.flush()
        created: list[EpisodePlan] = []
        for data in sorted(curriculum.episodes, key=lambda item: item.position):
            invalid_prerequisites = [
                value for value in data.prerequisite_positions if value >= data.position
            ]
            if invalid_prerequisites:
                raise ValueError("前提Episodeは現在の話数より前だけ指定できます")
            episode = EpisodePlan(
                series_plan_id=series.id,
                position=data.position,
                title=data.title,
                summary=data.summary,
                learning_objectives=data.learning_objectives,
                prerequisite_positions=data.prerequisite_positions,
                new_concepts=data.new_concepts,
                review_concepts=data.review_concepts,
                excluded_concepts=data.excluded_concepts,
                demo_outline=data.demo_outline,
                exercise_outline=data.exercise_outline,
                next_episode_bridge=data.next_episode_bridge,
                target_duration_seconds=data.target_duration_seconds,
                status="draft",
            )
            session.add(episode)
            created.append(episode)
        series.status = "curriculum_draft"
        session.flush()
        return created

    job_result = await run_idempotent_async(
        session,
        job_type="generate_curriculum",
        entity_type="series_plan",
        entity_id=series.id,
        idempotency_key=idempotency_key,
        fn=_do_generate,
    )
    if job_result.status == "in_progress":
        raise JobInProgressError("curriculum generation already in progress")
    if job_result.status == "skipped":
        return (
            session.query(EpisodePlan)
            .filter(EpisodePlan.series_plan_id == series.id)
            .order_by(EpisodePlan.position)
            .all()
        )
    assert job_result.result is not None
    return job_result.result


def approve_curriculum(session: Session, series: SeriesPlan) -> None:
    episodes = session.query(EpisodePlan).filter(EpisodePlan.series_plan_id == series.id).all()
    if len(episodes) != series.planned_episode_count:
        raise ValueError("計画した話数が揃っていません")
    for episode in episodes:
        episode.status = "approved"
    series.status = "approved"


def create_topic_from_episode(
    session: Session, *, series: SeriesPlan, episode: EpisodePlan
) -> tuple[Topic, str]:
    if series.status not in {"approved", "active"} or episode.status not in {
        "approved",
        "topic_created",
    }:
        raise ValueError("承認済みカリキュラムのEpisodeだけ制作開始できます")
    prerequisites = (
        session.query(EpisodePlan)
        .filter(
            EpisodePlan.series_plan_id == series.id,
            EpisodePlan.position.in_(episode.prerequisite_positions or []),
        )
        .all()
    )
    missing_prerequisites = [item.position for item in prerequisites if not item.topic_id]
    if len(prerequisites) != len(set(episode.prerequisite_positions or [])):
        raise ValueError("存在しない前提Episodeが指定されています")
    if missing_prerequisites:
        raise ValueError(
            f"先に前提Episodeの制作を開始してください: {sorted(missing_prerequisites)}"
        )
    if episode.topic_id:
        topic = session.get(Topic, episode.topic_id)
        if topic is None:
            raise RuntimeError("Episodeに紐付くTopicが見つかりません")
    else:
        description = (
            f"シリーズ「{series.name}」第{episode.position}回。\n{episode.summary}\n"
            f"学習目標: {', '.join(episode.learning_objectives)}\n"
            f"新規概念: {', '.join(episode.new_concepts)}\n"
            f"扱わない概念: {', '.join(episode.excluded_concepts)}\n"
            f"デモ: {episode.demo_outline}\n演習: {episode.exercise_outline}"
        )
        topic = Topic(
            channel_id=series.channel_id,
            title=f"第{episode.position}回 {episode.title}",
            description=description,
            source_type="derived",
            source_ref=f"series:{series.id}:episode:{episode.id}",
        )
        session.add(topic)
        session.flush()
        episode.topic_id = topic.id
        episode.status = "topic_created"
        series.status = "active"
    project = _get_or_create_video_project(session, topic_id=topic.id)
    target = episode.target_duration_seconds
    project.production_settings = ProductionSettings(
        preset="custom",
        video_format="custom",
        target_duration_seconds=target,
        min_duration_seconds=round(target * 0.85),
        max_duration_seconds=round(target * 1.15),
        min_sections=3,
        max_sections=8,
        script_template="explainer",
        tone="初心者に寄り添い、前提知識を飛ばさず丁寧に",
    ).model_dump()
    session.flush()
    return topic, project.id
