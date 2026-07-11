from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_200_with_database_and_media_tool_status(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    # FFmpeg未検出でも管理画面は利用可能なためHTTP 200を維持する。
    # statusはメディアツール可用性で ok / degraded に変わる。
    assert body["database"] == "ok"
    assert body["status"] in ("ok", "degraded")
    assert body["ffmpeg"] in ("ok", "missing")
    assert body["ffprobe"] in ("ok", "missing")
    assert isinstance(body["ready_for_rendering"], bool)
    assert body["ready_for_rendering"] == (body["ffmpeg"] == "ok" and body["ffprobe"] == "ok")
    assert (body["status"] == "ok") == body["ready_for_rendering"]
