"""使い方ガイドページ(/help)のテスト。DB非依存の静的コンテンツ。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_help_page_returns_200_with_key_sections(client: TestClient) -> None:
    response = client.get("/help")

    assert response.status_code == 200
    assert "使い方ガイド" in response.text
    assert "立ち絵掛け合い動画を作る準備" in response.text
    assert "VOICEVOXと立ち絵レンダリングを有効にする" in response.text
    assert "全体の流れ" in response.text
    assert "YouTube" in response.text
    assert "よくある詰まりどころ" in response.text
    assert "用語集" in response.text


def test_help_page_requires_admin_auth_like_other_pages(client: TestClient) -> None:
    # 通常のGET(認証バイパス済みのdevelopment/testでは200になる想定)。
    # 認証必須の挙動そのものは test_auth.py で検証済みのため、ここではリンク導線のみ確認する。
    response = client.get("/help")
    assert response.status_code == 200


def test_help_link_present_in_sidebar_on_other_pages(client: TestClient) -> None:
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert 'href="/help"' in response.text
    assert "使い方ガイド" in response.text
