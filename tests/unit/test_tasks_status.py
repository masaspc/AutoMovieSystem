"""`/tasks/{task_id}/status` (HTMXポーリング用)の挙動を検証する。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient


@dataclass
class _FakeAsyncResult:
    state: str


def test_task_status_pending_returns_banner_fragment(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.web.tasks_status.celery_app.AsyncResult",
        lambda task_id: _FakeAsyncResult(state="STARTED"),
    )

    response = client.get(
        "/tasks/task-1/status", params={"redirect_url": "/topics/abc", "label": "台本生成"}
    )

    assert response.status_code == 200
    assert "台本生成" in response.text
    assert 'hx-get="/tasks/task-1/status' in response.text


def test_task_status_success_redirects_via_hx_redirect_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.web.tasks_status.celery_app.AsyncResult",
        lambda task_id: _FakeAsyncResult(state="SUCCESS"),
    )

    response = client.get(
        "/tasks/task-1/status", params={"redirect_url": "/topics/abc", "label": "台本生成"}
    )

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/topics/abc"


def test_task_status_failure_redirects_with_error_message(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.web.tasks_status.celery_app.AsyncResult",
        lambda task_id: _FakeAsyncResult(state="FAILURE"),
    )

    response = client.get(
        "/tasks/task-1/status", params={"redirect_url": "/topics/abc", "label": "台本生成"}
    )

    assert response.status_code == 200
    redirect = response.headers["HX-Redirect"]
    assert redirect.startswith("/topics/abc?error=")
