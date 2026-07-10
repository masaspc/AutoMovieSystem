"""グロース機能(成長サマリー/ベンチマーク/派生企画/画面)のテスト。"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.benchmark_video import BenchmarkVideo
from app.models.channel import Channel
from app.models.publication import Publication
from app.models.topic import Topic
from app.models.video_metric_daily import VideoMetricDaily
from app.models.video_project import VideoProject
from app.services.growth import (
    compute_growth_summary,
    derive_sequel_topic,
    derive_topic_from_benchmark,
    list_video_performance,
    register_benchmark_video,
)

pytestmark = pytest.mark.usefixtures("_clear_settings_cache")


def _make_channel(db_session: Session) -> Channel:
    channel = Channel(name="growth-channel")
    db_session.add(channel)
    db_session.flush()
    return channel


def _make_publication(
    db_session: Session, channel: Channel, *, title: str = "動画A", ref: str = "t-1"
) -> Publication:
    topic = Topic(channel_id=channel.id, title=title, source_type="manual", source_ref=ref)
    db_session.add(topic)
    db_session.flush()
    project = VideoProject(topic_id=topic.id, status="UPLOADED_PRIVATE", generation=1)
    db_session.add(project)
    db_session.flush()
    publication = Publication(
        video_project_id=project.id,
        title=title,
        description="",
        tags=[],
        privacy_status="private",
        idempotency_key=f"upload:{project.id}:x-{ref}",
        upload_status="completed",
        youtube_video_id=f"fake-{ref}",
    )
    db_session.add(publication)
    db_session.flush()
    return publication


def _add_metrics(
    db_session: Session,
    publication: Publication,
    *,
    metric_date: date,
    views: int,
    gained: int,
    lost: int = 0,
    revenue: float = 0.0,
) -> None:
    db_session.add(
        VideoMetricDaily(
            publication_id=publication.id,
            metric_date=metric_date,
            views=views,
            subscribers_gained=gained,
            subscribers_lost=lost,
            estimated_revenue=revenue,
        )
    )
    db_session.flush()


def test_growth_summary_aggregates_net_subscribers_and_progress(db_session: Session) -> None:
    channel = _make_channel(db_session)
    pub = _make_publication(db_session, channel)
    _add_metrics(db_session, pub, metric_date=date(2026, 7, 1), views=1000, gained=60, lost=10)
    _add_metrics(db_session, pub, metric_date=date(2026, 7, 2), views=500, gained=50, revenue=1.5)

    summary = compute_growth_summary(db_session)

    assert summary.subscribers_net == 100
    assert summary.subscriber_target == 1000
    assert summary.progress_ratio == pytest.approx(0.1)
    assert summary.total_views == 1500
    assert summary.total_estimated_revenue == pytest.approx(1.5)
    assert summary.uploads_completed == 1


def test_video_performance_sorted_by_subs_efficiency(db_session: Session) -> None:
    channel = _make_channel(db_session)
    low = _make_publication(db_session, channel, title="低効率", ref="low")
    high = _make_publication(db_session, channel, title="高効率", ref="high")
    _add_metrics(db_session, low, metric_date=date(2026, 7, 1), views=10_000, gained=10)
    _add_metrics(db_session, high, metric_date=date(2026, 7, 1), views=1_000, gained=30)

    performances = list_video_performance(db_session)

    assert [p.title for p in performances[:2]] == ["高効率", "低効率"]
    assert performances[0].subs_per_1k_views == pytest.approx(30.0)


def test_register_benchmark_is_get_or_create(db_session: Session) -> None:
    channel = _make_channel(db_session)

    first, created_first = register_benchmark_video(
        db_session,
        channel_id=channel.id,
        title="伸びてる動画",
        channel_name="他チャンネル",
        url="https://www.youtube.com/watch?v=abc",
        format_tags=["ランキング"],
    )
    second, created_second = register_benchmark_video(
        db_session,
        channel_id=channel.id,
        title="別タイトルでも同URL",
        channel_name="他チャンネル",
        url="https://www.youtube.com/watch?v=abc",
    )

    assert created_first is True
    assert created_second is False
    assert first.id == second.id
    assert db_session.query(BenchmarkVideo).count() == 1


def test_derive_topic_from_benchmark_is_idempotent(db_session: Session) -> None:
    channel = _make_channel(db_session)
    benchmark, _ = register_benchmark_video(
        db_session,
        channel_id=channel.id,
        title="伸びてる動画",
        channel_name="他チャンネル",
        url="https://www.youtube.com/watch?v=abc",
        notes="冒頭で結論を言う構成が強い",
        format_tags=["冒頭結論"],
    )

    topic, created = derive_topic_from_benchmark(db_session, benchmark_id=benchmark.id)
    again, created_again = derive_topic_from_benchmark(db_session, benchmark_id=benchmark.id)

    assert created is True
    assert created_again is False
    assert topic.id == again.id
    assert topic.source_type == "benchmark"
    assert "差別化" in topic.title
    assert "転載" in (topic.description or "")
    assert db_session.query(Topic).filter(Topic.source_type == "benchmark").count() == 1


def test_derive_sequel_topic_is_idempotent(db_session: Session) -> None:
    channel = _make_channel(db_session)
    publication = _make_publication(db_session, channel, title="勝ち動画")

    topic, created = derive_sequel_topic(db_session, publication_id=publication.id)
    again, created_again = derive_sequel_topic(db_session, publication_id=publication.id)

    assert created is True
    assert created_again is False
    assert topic.id == again.id
    assert topic.source_type == "derived"
    assert "続編" in topic.title


def test_growth_and_benchmarks_pages_render(client: TestClient, db_session: Session) -> None:
    channel = _make_channel(db_session)
    pub = _make_publication(db_session, channel)
    _add_metrics(db_session, pub, metric_date=date(2026, 7, 1), views=1000, gained=5)
    db_session.commit()

    growth = client.get("/growth")
    benchmarks = client.get("/benchmarks")

    assert growth.status_code == 200
    assert "登録者" in growth.text
    assert "量産バッチ" in growth.text
    assert benchmarks.status_code == 200
    assert "差別化企画" in benchmarks.text or "参考動画" in benchmarks.text


def test_benchmark_register_and_derive_via_web(client: TestClient, db_session: Session) -> None:
    channel = _make_channel(db_session)
    db_session.commit()

    get_response = client.get("/benchmarks")
    csrf_token = get_response.cookies["csrf_token"]

    create_response = client.post(
        "/benchmarks",
        data={
            "csrf_token": csrf_token,
            "channel_id": channel.id,
            "title": "参考動画X",
            "channel_name": "参考チャンネル",
            "url": "https://www.youtube.com/watch?v=xyz",
            "format_tags": "比較表, 冒頭結論",
        },
        follow_redirects=False,
    )
    assert create_response.status_code == 303

    benchmark = db_session.query(BenchmarkVideo).one()
    assert benchmark.format_tags == ["比較表", "冒頭結論"]

    derive_response = client.post(
        f"/benchmarks/{benchmark.id}/derive-topic",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert derive_response.status_code == 303
    assert db_session.query(Topic).filter(Topic.source_type == "benchmark").count() == 1


def test_benchmark_rejects_non_youtube_or_unsafe_url(
    client: TestClient, db_session: Session
) -> None:
    channel = _make_channel(db_session)
    db_session.commit()
    csrf_token = client.get("/benchmarks").cookies["csrf_token"]

    response = client.post(
        "/benchmarks",
        data={
            "csrf_token": csrf_token,
            "channel_id": channel.id,
            "title": "不正URL",
            "channel_name": "参考チャンネル",
            "url": "javascript:alert(1)",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert db_session.query(BenchmarkVideo).count() == 0


def test_growth_batch_post_without_csrf_is_forbidden(
    client: TestClient, db_session: Session
) -> None:
    channel = _make_channel(db_session)
    db_session.commit()

    response = client.post(
        "/growth/production-batch",
        data={"channel_id": channel.id, "limit": "1", "csrf_token": "invalid"},
        follow_redirects=False,
    )
    assert response.status_code == 403
