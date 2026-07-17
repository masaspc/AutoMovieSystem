from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.channel import Channel


def test_settings_page_edits_channel_editorial_policy(
    client: TestClient, db_session: Session
) -> None:
    channel = Channel(name="金融チャンネル")
    db_session.add(channel)
    db_session.commit()

    page = client.get("/settings")
    assert page.status_code == 200
    assert "チャンネル別編集方針" in page.text
    csrf_token = page.cookies["csrf_token"]
    response = client.post(
        f"/settings/channels/{channel.id}/editorial-policy",
        data={
            "csrf_token": csrf_token,
            "tone": "落ち着いた丁寧解説",
            "target_audience": "投資未経験者",
            "prohibited_instructions": "個別銘柄を推奨しない\n売買を助言しない",
            "disclaimer_text": "本動画は情報提供のみを目的としています。",
            "trend_feed_urls": "https://www.fsa.go.jp/news/rss.xml",
            "default_production_settings_json": '{"preset":"standard_5min"}',
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    db_session.refresh(channel)
    assert channel.editorial_policy is not None
    assert channel.editorial_policy["tone"] == "落ち着いた丁寧解説"
    assert channel.editorial_policy["prohibited_instructions"] == [
        "個別銘柄を推奨しない",
        "売買を助言しない",
    ]


def test_settings_policy_update_requires_valid_csrf(
    client: TestClient, db_session: Session
) -> None:
    channel = Channel(name="金融チャンネル")
    db_session.add(channel)
    db_session.commit()
    response = client.post(
        f"/settings/channels/{channel.id}/editorial-policy",
        data={"csrf_token": "invalid"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    db_session.refresh(channel)
    assert channel.editorial_policy is None
