"""セルフレビューのWeb起動導線とInsight削除オプトアウト。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.insight import Insight
from app.models.publication import Publication
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.services.feedback.self_review import collect_recent_lessons


@dataclass
class _FakeAsyncResult:
    id: str


def _uploaded_publication(
    db_session: Session, *, source_ref: str = "self-review-web"
) -> Publication:
    channel = Channel(name=f"channel-{source_ref}")
    db_session.add(channel)
    db_session.flush()
    topic = Topic(
        channel_id=channel.id,
        title="投稿済み動画",
        source_type="manual",
        source_ref=source_ref,
    )
    db_session.add(topic)
    db_session.flush()
    project = VideoProject(
        topic_id=topic.id,
        status="UPLOADED_PRIVATE",
        generation=1,
    )
    db_session.add(project)
    db_session.flush()
    publication = Publication(
        video_project_id=project.id,
        youtube_video_id=f"video-{source_ref}"[:32],
        title="投稿済み動画",
        description="説明",
        privacy_status="private",
        idempotency_key=f"upload:{source_ref}",
        upload_status="completed",
    )
    db_session.add(publication)
    db_session.commit()
    return publication


def test_publications_page_dispatches_self_review_with_task_banner(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = _uploaded_publication(db_session)
    dispatched: list[str] = []

    def _delay(publication_id: str) -> _FakeAsyncResult:
        dispatched.append(publication_id)
        return _FakeAsyncResult(id="task-self-review")

    monkeypatch.setattr("app.web.publications_page.run_self_review_task.delay", _delay)

    get_response = client.get("/publications")
    assert "自己レビューを実行" in get_response.text
    csrf_token = get_response.cookies["csrf_token"]
    response = client.post(
        f"/publications/{publication.id}/self-review",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    assert location.path == "/publications"
    assert parse_qs(location.query) == {
        "task_id": ["task-self-review"],
        "task_label": ["自己レビュー"],
    }
    assert dispatched == [publication.id]


def test_video_project_detail_dispatches_publication_self_review(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = _uploaded_publication(db_session, source_ref="detail")
    project_id = publication.video_project_id
    dispatched: list[str] = []

    def _delay(publication_id: str) -> _FakeAsyncResult:
        dispatched.append(publication_id)
        return _FakeAsyncResult(id="task-detail-review")

    monkeypatch.setattr("app.web.video_projects.run_self_review_task.delay", _delay)

    get_response = client.get(f"/video-projects/{project_id}")
    assert "自己レビューを実行" in get_response.text
    csrf_token = get_response.cookies["csrf_token"]
    response = client.post(
        f"/video-projects/{project_id}/self-review",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    assert location.path == f"/video-projects/{project_id}"
    assert parse_qs(location.query) == {
        "task_id": ["task-detail-review"],
        "task_label": ["自己レビュー"],
    }
    assert dispatched == [publication.id]


def test_self_review_dispatch_rejects_missing_entities(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def _delay(publication_id: str) -> _FakeAsyncResult:
        nonlocal called
        called = True
        return _FakeAsyncResult(id=publication_id)

    monkeypatch.setattr("app.web.publications_page.run_self_review_task.delay", _delay)
    csrf_token = client.get("/publications").cookies["csrf_token"]
    response = client.post(
        "/publications/missing/self-review",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert response.status_code == 404
    assert called is False


def test_deleting_self_review_insight_removes_it_from_prompt_candidates(
    client: TestClient,
    db_session: Session,
) -> None:
    publication = _uploaded_publication(db_session, source_ref="delete")
    project = db_session.get(VideoProject, publication.video_project_id)
    assert project is not None
    topic = db_session.get(Topic, project.topic_id)
    assert topic is not None

    insight = Insight(
        source_type="publication",
        source_id=publication.id,
        insight_type="self_review",
        source_ref=f"self_review:{publication.id}:2026-07-12:0",
        finding="冒頭で離脱している",
        evidence={"metric_date": "2026-07-12"},
        confidence=0.6,
        recommended_action="冒頭5秒で結論を示してください",
        human_review_reason="自動振り返り",
    )
    db_session.add(insight)
    db_session.commit()
    insight_id = insight.id
    assert collect_recent_lessons(db_session, channel_id=topic.channel_id) == [insight]

    get_response = client.get("/insights", params={"insight_type": "self_review"})
    assert "削除すると、今後の台本への自動反映から外れます" in get_response.text
    csrf_token = get_response.cookies["csrf_token"]
    response = client.post(
        f"/insights/{insight_id}/delete",
        data={"csrf_token": csrf_token, "insight_type": "self_review"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert db_session.get(Insight, insight_id) is None
    assert collect_recent_lessons(db_session, channel_id=topic.channel_id) == []


def test_delete_insight_requires_valid_csrf(
    client: TestClient,
    db_session: Session,
) -> None:
    publication = _uploaded_publication(db_session, source_ref="csrf-delete")
    insight = Insight(
        source_type="publication",
        source_id=publication.id,
        insight_type="self_review",
        source_ref=f"self_review:{publication.id}:2026-07-12:0",
        finding="改善点",
        evidence={},
        confidence=0.6,
        recommended_action="改善する",
        human_review_reason="自動振り返り",
    )
    db_session.add(insight)
    db_session.commit()

    response = client.post(
        f"/insights/{insight.id}/delete",
        data={"csrf_token": "invalid"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert db_session.get(Insight, insight.id) is not None
