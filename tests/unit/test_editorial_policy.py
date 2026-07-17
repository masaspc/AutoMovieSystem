from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.channel import Channel
from app.schemas.editorial_policy import ChannelEditorialPolicy
from app.services.channels.policy import get_editorial_policy


def test_editorial_policy_accepts_empty_partial_and_full_values() -> None:
    assert ChannelEditorialPolicy().model_dump() == {
        "tone": "",
        "target_audience": "",
        "prohibited_instructions": [],
        "disclaimer_text": "",
        "trend_feed_urls": [],
        "default_production_settings": {},
    }
    partial = ChannelEditorialPolicy(tone=" 丁寧 ")
    assert partial.tone == "丁寧"
    full = ChannelEditorialPolicy(
        tone="落ち着いた解説",
        target_audience="投資未経験者",
        prohibited_instructions=["銘柄を推奨しない", "銘柄を推奨しない"],
        disclaimer_text="本動画は情報提供のみです。",
        trend_feed_urls=["https://www.fsa.go.jp/news/rss.xml"],
        default_production_settings={"preset": "standard_5min"},
    )
    assert full.prohibited_instructions == ["銘柄を推奨しない"]
    assert full.default_production_settings["preset"] == "standard_5min"


def test_editorial_policy_rejects_invalid_feed_url() -> None:
    with pytest.raises(ValidationError, match=r"http\(s\)"):
        ChannelEditorialPolicy(trend_feed_urls=["file:///etc/passwd"])


def test_invalid_stored_policy_falls_back_to_empty(caplog: pytest.LogCaptureFixture) -> None:
    channel = Channel(id="channel-1", name="ch", editorial_policy="broken")  # type: ignore[arg-type]
    policy = get_editorial_policy(channel)
    assert policy == ChannelEditorialPolicy()
    assert "channel_editorial_policy_invalid" in caplog.text
