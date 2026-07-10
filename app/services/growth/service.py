"""グロース機能(登録者0→1000目標のサイト構成)。

3つの責務を持つ:
1. 成長サマリー(登録者純増の進捗・投稿ペース・動画別パフォーマンス集計)
2. 企画の増殖(ベンチマーク動画からの模倣+差別化企画、勝ち動画からの続編企画)
3. 量産バッチ(スコア上位の企画を自動レビューまでまとめて制作。承認は人間のまま)

人為的な再生・登録・評価・コメントを発生させる機能は実装しない(CLAUDE.md絶対原則)。
ベンチマークは「フォーマットの研究・模倣」であり、コンテンツの転載ではない
(docs/content-policy.md)。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.benchmark_video import BenchmarkVideo
from app.models.publication import Publication
from app.models.topic import Topic
from app.models.video_metric_daily import VideoMetricDaily
from app.models.video_project import VideoProject
from app.services.orchestration import (
    PipelineProviders,
    ProductionResult,
    run_production_pipeline,
)

logger = get_logger(__name__)

_ALLOWED_BENCHMARK_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


def _validate_benchmark_url(url: str) -> str:
    """ベンチマーク対象をYouTubeのHTTPS/HTTP URLに限定する。"""
    normalized = url.strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in _ALLOWED_BENCHMARK_HOSTS:
        raise ValueError("ベンチマークURLにはYouTubeのhttp(s) URLを指定してください")
    return normalized


# 量産バッチの対象となる「まだ制作が完了していない」プロジェクト状態。
# これ以降(レビュー合格〜公開)へ進んだ企画はバッチ対象から外す。
_PRODUCIBLE_PROJECT_STATUSES = (
    "TOPIC_CREATED",
    "TOPIC_SCORED",
    "RESEARCH_READY",
    "SCRIPT_GENERATED",
    "SCRIPT_REVIEWED",
    "ASSETS_READY",
    "VIDEO_RENDERED",
    "RENDER_FAILED",
    "REVIEW_FAILED",
    "SCRIPT_FAILED",
    "ASSET_FAILED",
)


@dataclass(frozen=True)
class GrowthSummary:
    """成長ダッシュボードのKPI。"""

    subscribers_net: int
    subscriber_target: int
    progress_ratio: float
    total_views: int
    total_estimated_revenue: float
    uploads_completed: int
    uploads_last_28_days: int
    weekly_pace: float


@dataclass(frozen=True)
class VideoPerformance:
    """動画別パフォーマンス(勝ちパターン特定用)。"""

    publication_id: str
    video_project_id: str
    title: str
    views: int
    ctr: float
    average_view_percentage: float
    subscribers_gained: int
    estimated_revenue: float
    subs_per_1k_views: float


@dataclass
class BatchProductionReport:
    """量産バッチの実行結果。"""

    attempted: int = 0
    review_passed: int = 0
    results: list[ProductionResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def compute_growth_summary(session: Session, *, channel_id: str | None = None) -> GrowthSummary:
    """登録者純増・再生・収益・投稿ペースを集計する(全期間+直近28日)。"""
    settings = get_settings()
    target = settings.GROWTH_SUBSCRIBER_TARGET

    metric_query = session.query(
        func.coalesce(func.sum(VideoMetricDaily.subscribers_gained), 0),
        func.coalesce(func.sum(VideoMetricDaily.subscribers_lost), 0),
        func.coalesce(func.sum(VideoMetricDaily.views), 0),
        func.coalesce(func.sum(VideoMetricDaily.estimated_revenue), 0.0),
    )
    gained, lost, views, revenue = metric_query.one()
    subscribers_net = int(gained) - int(lost)

    uploads_query = session.query(func.count(Publication.id)).filter(
        Publication.upload_status == "completed"
    )
    uploads_completed = int(uploads_query.scalar() or 0)

    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=28)
    recent_query = session.query(func.count(Publication.id)).filter(
        Publication.upload_status == "completed", Publication.created_at >= since
    )
    uploads_last_28_days = int(recent_query.scalar() or 0)

    return GrowthSummary(
        subscribers_net=subscribers_net,
        subscriber_target=target,
        progress_ratio=(subscribers_net / target) if target > 0 else 0.0,
        total_views=int(views),
        total_estimated_revenue=float(revenue),
        uploads_completed=uploads_completed,
        uploads_last_28_days=uploads_last_28_days,
        weekly_pace=round(uploads_last_28_days / 4.0, 1),
    )


def list_video_performance(session: Session, *, limit: int = 20) -> list[VideoPerformance]:
    """動画別の累計パフォーマンス(登録効率順)。勝ちパターン特定に使う。"""
    rows = (
        session.query(
            Publication.id,
            Publication.video_project_id,
            Publication.title,
            func.coalesce(func.sum(VideoMetricDaily.views), 0).label("views"),
            func.coalesce(func.avg(VideoMetricDaily.ctr), 0.0).label("ctr"),
            func.coalesce(func.avg(VideoMetricDaily.average_view_percentage), 0.0).label(
                "retention"
            ),
            func.coalesce(func.sum(VideoMetricDaily.subscribers_gained), 0).label("subs"),
            func.coalesce(func.sum(VideoMetricDaily.estimated_revenue), 0.0).label("revenue"),
        )
        .outerjoin(VideoMetricDaily, VideoMetricDaily.publication_id == Publication.id)
        .filter(Publication.upload_status == "completed")
        .group_by(Publication.id, Publication.video_project_id, Publication.title)
        .all()
    )

    performances = [
        VideoPerformance(
            publication_id=row[0],
            video_project_id=row[1],
            title=row[2],
            views=int(row[3]),
            ctr=float(row[4]),
            average_view_percentage=float(row[5]),
            subscribers_gained=int(row[6]),
            estimated_revenue=float(row[7]),
            subs_per_1k_views=(float(row[6]) * 1000.0 / float(row[3])) if row[3] else 0.0,
        )
        for row in rows
    ]
    performances.sort(key=lambda p: (p.subs_per_1k_views, p.views), reverse=True)
    return performances[:limit]


def register_benchmark_video(
    session: Session,
    *,
    channel_id: str,
    title: str,
    channel_name: str,
    url: str,
    views: int | None = None,
    subscribers: int | None = None,
    notes: str | None = None,
    format_tags: list[str] | None = None,
) -> tuple[BenchmarkVideo, bool]:
    """ベンチマーク動画のget-or-create(UNIQUE(channel_id, url))。(entity, created)を返す。"""
    url = _validate_benchmark_url(url)
    existing = (
        session.query(BenchmarkVideo)
        .filter(BenchmarkVideo.channel_id == channel_id, BenchmarkVideo.url == url)
        .one_or_none()
    )
    if existing is not None:
        return existing, False

    benchmark = BenchmarkVideo(
        channel_id=channel_id,
        title=title,
        channel_name=channel_name,
        url=url,
        views=views,
        subscribers=subscribers,
        notes=notes,
        format_tags=format_tags or [],
    )
    session.add(benchmark)
    session.flush()
    return benchmark, True


def derive_topic_from_benchmark(session: Session, *, benchmark_id: str) -> tuple[Topic, bool]:
    """ベンチマーク動画から模倣+差別化の企画候補を作る(get-or-create、冪等)。

    転載ではなく「成功フォーマットを踏襲した独自コンテンツ」の企画として登録する。
    source_ref=ベンチマークURLのハッシュにより同一ベンチマークからの重複作成を防ぐ。
    """
    benchmark = session.get(BenchmarkVideo, benchmark_id)
    if benchmark is None:
        raise ValueError(f"BenchmarkVideo not found: {benchmark_id}")

    source_ref = hashlib.sha256(benchmark.url.encode()).hexdigest()[:64]
    existing = (
        session.query(Topic)
        .filter(
            Topic.channel_id == benchmark.channel_id,
            Topic.source_type == "benchmark",
            Topic.source_ref == source_ref,
        )
        .one_or_none()
    )
    if existing is not None:
        return existing, False

    tags = "、".join(benchmark.format_tags or []) or "(未設定)"
    description_parts = [
        f"ベンチマーク「{benchmark.title}」({benchmark.channel_name})のフォーマットを踏襲し、",
        "独自の切り口・独自の検証結果で差別化した動画を制作する。",
        f"参考にする要素: {tags}。",
        "転載・流用は禁止。構成(フック/展開/結論の型)のみを参考にする。",
    ]
    if benchmark.notes:
        description_parts.append(f"運用メモ: {benchmark.notes}")

    topic = Topic(
        channel_id=benchmark.channel_id,
        title=f"{benchmark.title}(差別化版)",
        description="".join(description_parts),
        source_type="benchmark",
        source_url=benchmark.url,
        source_ref=source_ref,
        # ベンチマーク由来は需要が実証済みのため需要スコアを高めに初期化する
        # (最終スコアは score_topic の設定可能な重みで計算される)。
        demand_score=8.0,
        revenue_score=6.0,
        originality_score=4.0,
        expertise_score=5.0,
        freshness_score=6.0,
        production_cost_score=6.0,
    )
    session.add(topic)
    session.flush()
    logger.info("benchmark_topic_derived", benchmark_id=benchmark.id, topic_id=topic.id)
    return topic, True


def derive_sequel_topic(session: Session, *, publication_id: str) -> tuple[Topic, bool]:
    """勝ち動画(高パフォーマンス)から続編企画を作る(get-or-create、冪等)。"""
    publication = session.get(Publication, publication_id)
    if publication is None:
        raise ValueError(f"Publication not found: {publication_id}")

    project = session.get(VideoProject, publication.video_project_id)
    origin_topic = session.get(Topic, project.topic_id) if project else None
    if origin_topic is None:
        raise ValueError(f"origin Topic not found for publication: {publication_id}")

    source_ref = f"sequel:{publication.id}"
    existing = (
        session.query(Topic)
        .filter(
            Topic.channel_id == origin_topic.channel_id,
            Topic.source_type == "derived",
            Topic.source_ref == source_ref,
        )
        .one_or_none()
    )
    if existing is not None:
        return existing, False

    topic = Topic(
        channel_id=origin_topic.channel_id,
        title=f"{publication.title} 続編",
        description=(
            f"高パフォーマンス動画「{publication.title}」の続編。"
            "前作で反応が良かった構成・テーマを深掘りし、視聴者の次の疑問に答える。"
        ),
        source_type="derived",
        source_ref=source_ref,
        demand_score=8.0,
        revenue_score=6.0,
        originality_score=6.0,
        expertise_score=6.0,
        freshness_score=7.0,
        production_cost_score=6.0,
    )
    session.add(topic)
    session.flush()
    logger.info("sequel_topic_derived", publication_id=publication.id, topic_id=topic.id)
    return topic, True


def _producible_topics(session: Session, *, channel_id: str, limit: int) -> list[Topic]:
    """量産対象: 制作が完了していない企画をスコア降順で選ぶ(rejectedは除外)。"""
    candidates = (
        session.query(Topic)
        .filter(Topic.channel_id == channel_id, Topic.status != "rejected")
        .order_by(Topic.total_score.desc(), Topic.created_at.asc())
        .all()
    )

    selected: list[Topic] = []
    for topic in candidates:
        project = (
            session.query(VideoProject)
            .filter(VideoProject.topic_id == topic.id, VideoProject.generation == 1)
            .one_or_none()
        )
        if project is not None and project.status not in _PRODUCIBLE_PROJECT_STATUSES:
            continue
        selected.append(topic)
        if len(selected) >= limit:
            break
    return selected


async def run_production_batch(
    session: Session,
    *,
    channel_id: str,
    providers: PipelineProviders,
    limit: int = 3,
) -> BatchProductionReport:
    """スコア上位の未制作企画を、自動レビュー通過までまとめて制作する(量産バッチ)。

    承認・アップロードは含めない(人間承認 fail-closed を維持)。各企画の制作は
    既存の冪等パイプラインなので、バッチの再実行で重複制作は起きない。
    """
    report = BatchProductionReport()
    for topic in _producible_topics(session, channel_id=channel_id, limit=limit):
        report.attempted += 1
        try:
            result = await run_production_pipeline(session, topic_id=topic.id, providers=providers)
        except Exception as exc:  # noqa: BLE001 - 1件の失敗でバッチ全体を止めない
            session.rollback()
            report.errors.append(f"{topic.title}: {type(exc).__name__}")
            logger.warning(
                "batch_production_item_failed", topic_id=topic.id, error_type=type(exc).__name__
            )
            continue
        report.results.append(result)
        if result.review_passed:
            report.review_passed += 1

    logger.info(
        "batch_production_completed",
        channel_id=channel_id,
        attempted=report.attempted,
        review_passed=report.review_passed,
        errors=len(report.errors),
    )
    return report
