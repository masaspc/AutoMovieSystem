# YouTube OAuth 2.0 セットアップ手順

実YouTubeへアップロードする(`YOUTUBE_PROVIDER=real`)場合に必要な手順。
Fakeプロバイダー(デフォルト)ではこの手順は不要。

視聴維持率の場面分析には`yt-analytics.readonly`スコープも使用する。機能追加前に取得した
リフレッシュトークンにはこのスコープがないため、OAuthセットアップスクリプトを再実行する。

実シークレット(client_secret.json の中身・取得したリフレッシュトークン)は
このリポジトリに一切コミットしないこと。

## 1. GCPプロジェクトの準備

1. [Google Cloud Console](https://console.cloud.google.com/) で新規プロジェクトを作成する
   (または既存プロジェクトを使う)。
2. 「APIとサービス」→「有効なAPIとサービス」から **YouTube Data API v3** を有効化する。

## 2. OAuth同意画面の設定

1. 「APIとサービス」→「OAuth同意画面」でユーザータイプ(通常は「外部」)を選び、
   アプリ名・サポートメールなど必須項目を入力する。
2. スコープに以下を追加する(`app/providers/youtube/real.py` の `DEFAULT_SCOPES`):
   - `https://www.googleapis.com/auth/youtube.upload`
   - `https://www.googleapis.com/auth/youtube.force-ssl`
   - `https://www.googleapis.com/auth/youtube.readonly`
   - `https://www.googleapis.com/auth/yt-analytics.readonly`
3. テストユーザーとして運用アカウントを追加する(公開審査前は指定ユーザーのみ利用可能)。

## 3. OAuthクライアント(Desktop app)の作成

1. 「APIとサービス」→「認証情報」→「認証情報を作成」→「OAuthクライアントID」。
2. アプリケーションの種類は **デスクトップアプリ** を選択する
   (installed app flow。ループバックリダイレクト `http://127.0.0.1:<port>` を使うため、
   Webアプリ種別のリダイレクトURI事前登録は不要)。
3. 作成後に表示される `client_secret_xxxx.json` をダウンロードする。
   **このファイルはリポジトリにコミットしない**(`.gitignore` 済みの場所に保存すること)。

## 4. `.env` の設定

```
SECRET_ENCRYPTION_KEY=<十分に長いランダム文字列。本番は必ず安全に生成・管理する>
YOUTUBE_PROVIDER=real
YOUTUBE_OAUTH_CLIENT_ID=<client_secret.json の client_id>
YOUTUBE_OAUTH_CLIENT_SECRET=<client_secret.json の client_secret>
```

`SECRET_ENCRYPTION_KEY` が未設定の場合、`app/core/crypto.py` は暗号化・復号操作で
fail-fast例外を送出する(平文でのトークン保存を防ぐ)。

## 5. リフレッシュトークンの取得・保存

```
uv run python scripts/youtube_oauth_setup.py --client-secret path/to/client_secret_xxxx.json
```

1. スクリプト実行時にブラウザが自動的に開き、Googleアカウントでの認可を求められる。
2. 認可すると `http://127.0.0.1:<port>` へリダイレクトされ、スクリプトが認可コードを
   自動的に受け取る(コピー&ペースト不要。OOBフローは非推奨のため使用しない)。
3. 取得したリフレッシュトークンは `app/core/crypto.py`(Fernet)で暗号化した上で
   `OAuthToken` テーブルへ保存される。平文トークンは標準出力・ログに一切出力されない。

## 6. 検証

- `check_auth()` が `True` を返すこと(`app/providers/youtube/real.py`)。
- 初回アップロードは必ず `privacy_status=private` になることを確認する
  (`YOUTUBE_DEFAULT_PRIVACY_STATUS` の既定値)。
- 公開(`public`)は6条件ゲート(レビュー合格・人間承認・チェックサム一致・
  メタデータ確定・重複なし・有効なOAuth認証)をすべて満たし、かつ
  `AUTO_PUBLISH_ENABLED=true` の場合のみ行われる(既定は `false`)。

## トラブルシューティング

- **リフレッシュトークンが返らない**: 同一Googleアカウント・同一OAuthクライアントで
  過去に一度認可済みの場合、2回目以降はリフレッシュトークンが返らないことがある。
  [Googleアカウントの権限管理](https://myaccount.google.com/permissions) から
  当該アプリの許可を取り消してから再実行する。
- **`quotaExceeded`**: YouTube Data API v3 の既定クォータは1日10,000ユニット
  (videos.insert は1回1ユニットだが、日次アップロード上限は別途100件)。
  クォータ超過時はアプリ側で自動リトライせず処理を停止する(`QuotaExceededError`)。
