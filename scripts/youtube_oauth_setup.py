"""YouTube OAuth 2.0(installed app flow)セットアップスクリプト。

使い方:
    uv run python scripts/youtube_oauth_setup.py --client-secret path/to/client_secret.json

手順:
1. GCPで作成したOAuthクライアント(Desktop app種別)の `client_secret.json` を指定する。
2. ローカルループバック(`http://127.0.0.1:<port>`)でブラウザ認可コードを受け取る
   (`google-auth-oauthlib` の `InstalledAppFlow.run_local_server`。PKCE使用。
   OOBフローは非推奨のため使用しない:
   https://developers.google.com/identity/protocols/oauth2/native-app )。
3. 取得したリフレッシュトークンを `app/core/crypto.py`(Fernet)で暗号化し、
   `OAuthToken` としてDBへ保存する。

実行にはユーザーのブラウザ操作が必要なため、テストではロジック関数
(`build_flow` / `store_oauth_token`)のみを検証し、対話フローの `main()` は呼ばない。
平文のトークンはログ・標準出力に一切出力しない。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import encrypt_secret
from app.db.session import SessionLocal
from app.models.oauth_token import OAuthToken
from app.providers.youtube.real import DEFAULT_SCOPES


def build_flow(client_secret_path: Path, scopes: list[str] | None = None) -> InstalledAppFlow:
    """`client_secret.json` からOAuthフローを構築する(単体テストで検証可能な純粋関数)。"""
    return InstalledAppFlow.from_client_secrets_file(
        str(client_secret_path), scopes=list(scopes or DEFAULT_SCOPES)
    )


def store_oauth_token(
    session: Session,
    *,
    refresh_token: str,
    access_token: str | None,
    scopes: list[str],
    channel_id: str | None = None,
) -> OAuthToken:
    """取得したリフレッシュトークンをFernetで暗号化してDBへ保存する。"""
    token = OAuthToken(
        provider="youtube",
        channel_id=channel_id,
        encrypted_refresh_token=encrypt_secret(refresh_token),
        encrypted_access_token=encrypt_secret(access_token) if access_token else None,
        scopes=scopes,
    )
    session.add(token)
    session.flush()
    return token


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="YouTube OAuth 2.0 リフレッシュトークン取得")
    parser.add_argument(
        "--client-secret",
        required=True,
        type=Path,
        help="GCPからダウンロードしたclient_secret.json",
    )
    parser.add_argument("--channel-id", default=None, help="紐づけるChannel.id(任意)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    if not args.client_secret.exists():
        print(f"client_secret file not found: {args.client_secret}", file=sys.stderr)
        raise SystemExit(1)

    settings = get_settings()
    if not settings.SECRET_ENCRYPTION_KEY:
        print(
            "SECRET_ENCRYPTION_KEY is not configured. Set it in .env before running this script "
            "(fail-closed: refusing to store an unencrypted refresh token).",
            file=sys.stderr,
        )
        raise SystemExit(1)

    flow = build_flow(args.client_secret)
    print("ブラウザで認可画面を開きます。認可後、リフレッシュトークンを取得します...")
    credentials = flow.run_local_server(port=0)

    if not credentials.refresh_token:
        print(
            "リフレッシュトークンが取得できませんでした。Googleアカウントの既存の許可を"
            "取り消してから再実行してください(https://myaccount.google.com/permissions)。",
            file=sys.stderr,
        )
        raise SystemExit(1)

    session = SessionLocal()
    try:
        token = store_oauth_token(
            session,
            refresh_token=credentials.refresh_token,
            access_token=credentials.token,
            scopes=list(credentials.scopes or DEFAULT_SCOPES),
            channel_id=args.channel_id,
        )
        session.commit()
        print(f"OAuthToken saved: id={token.id} (トークン自体は暗号化済みでログには出力しません)")
    finally:
        session.close()


if __name__ == "__main__":
    main()
