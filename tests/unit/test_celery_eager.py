from __future__ import annotations

from app.workers.tasks.health import ping


def test_ping_returns_pong_in_eager_mode() -> None:
    result = ping.delay()
    assert result.get(timeout=5) == "pong"
