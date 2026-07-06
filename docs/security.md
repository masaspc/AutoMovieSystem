# セキュリティ対策・監査・本番移行

実装済みの防御機構と、本番環境への移行時の必須チェックリスト。

## 実装済み対策

### 1. 秘密管理

#### Fernet 対称暗号化

`app/core/crypto.py` で OAuthトークン・API キー等を暗号化:

```python
# .env の SECRET_ENCRYPTION_KEY から鍵導出
SECRET_ENCRYPTION_KEY=<Fernetキー>

# OAuthToken の refresh_token は DB へ暗号化して保存
encrypted_token = encrypt_secret(plaintext_refresh_token)
decrypted = decrypt_secret(encrypted_token)
```

**保証**:
- 256-bit symmetric key (SHA256 導出)
- tokens ファイルにも暗号化形式のみ保存
- ローカル開発でも暗号化(SECRET_ENCRYPTION_KEY 未設定時は fail-fast 例外)

#### .env 非コミット

`.gitignore`:
```
.env
.env.local
client_secret_*.json
```

本番 secret は `.env.example` に含めない(プレースホルダーのみ)。

**チェック**:
```powershell
git status
```

`.env`, `client_secret_*.json` が含まれていないことを確認。

### 2. ログマスキング

`app/core/logging.py` で structlog プロセッサ設定:

```python
# パターンマッチでシークレット自動除外
secrets = [
    settings.SECRET_ENCRYPTION_KEY,
    settings.ANTHROPIC_API_KEY,
    settings.YOUTUBE_OAUTH_CLIENT_SECRET,
]

# log 呼び出しでシークレットを含む値があれば "***MASKED***" に置換
logger.info("token_used", token=refresh_token)  # token=***MASKED***
```

**検証**:
- `app/core/logging.py` でマスキングプロセッサを全ハンドラに設定済み
- テスト実行時 stdout / ファイルログに secrets 非表示

### 3. Subprocess 実行安全性

`app/core/subprocess_util.py` で subprocess 実行を一元化:

```python
def run_checked(args: list[str], *, timeout: float | None = None, cwd: str | None = None) -> str:
    """
    - args は list のみ(shell=False 強制)
    - shell 脱出・コマンド注入を完全防止
    - timeout で無限実行を防止
    """
    result = subprocess.run(args, shell=False, timeout=timeout, ...)
```

**使用例**:
```python
# 正: 引数配列
run_checked(["ffmpeg", "-i", video_path, ...])

# 禁止: シェル文字列(build_checked が拒否)
run_checked("ffmpeg -i " + video_path)  # TypeError
```

**検証**: すべてのサブプロセス呼び出しが `run_checked` 経由(CI で ruff/mypy チェック)。

### 4. ファイルパス検証

`app/core/paths.py`:

```python
def validate_path_in_generated(path: str | Path) -> Path:
    """生成物パスは generated/ 配下に限定。ディレクトリトラバーサル防止。"""
    path = Path(path).resolve()
    generated = Path(settings.GENERATED_DIR).resolve()
    
    if not str(path).startswith(str(generated)):
        raise ValueError(f"Path {path} not in {generated}")
    return path
```

**例**:
```python
# 安全
Asset.file_path = "/app/generated/video_001.mp4"

# 拒否
Asset.file_path = "/etc/passwd"  # ValueError
Asset.file_path = "generated/../../etc/passwd"  # 解決後チェック
```

### 5. CSRF 防止

`app/web/common.py` / `app/web/approvals.py`:

```python
# 承認ボタン → POST with CSRF トークン
# FastAPI (Starlette) の MiddleWare で検証
@app.post("/approvals/{id}/approve")
async def approve_video_project(id: str, token: str = Form(...)):
    # token 検証(SECRET_ENCRYPTION_KEY から導出)
    if not verify_csrf_token(token):
        raise HTTPException(403, "CSRF token invalid")
    # Approval レコード作成
```

**使用**: Jinja2 テンプレート内で `csrf_token` をフォーム hidden フィールドに埋め込み。

### 6. 監査ログ

重要操作は `Approval` テーブルに記録:

```python
class Approval(Base):
    id: str
    video_project_id: str
    decision: str  # "approved" / "rejected"
    decided_by: str  # 実装時は環境変数・セッション user_id から取得可
    decided_at: datetime
```

**記録対象**:
- 承認 / 却下
- 公開スケジュール決定
- 予算変更(将来)

### 7. Fail-Closed デフォルト

```env
YOUTUBE_DEFAULT_PRIVACY_STATUS=private
AUTO_PUBLISH_ENABLED=false
REQUIRE_HUMAN_APPROVAL=true
```

**ポリシー**:
- 動画は常に private(意図的に public へ)
- 自動公開オフ(6条件 + フラグで有効化)
- 人間承認必須

### 8. 入力検証

FastAPI/Pydantic で自動検証:

```python
class TopicCreateRequest(BaseModel):
    title: str  # 必須
    description: str | None = None
    target_audience: str | None = None
    
    @field_validator("title")
    def title_not_empty(cls, v):
        if not v or len(v.strip()) == 0:
            raise ValueError("title cannot be empty")
        return v
```

### 9. SQL インジェクション防止

SQLAlchemy ORM 使用(全クエリ):

```python
# 安全: プレースホルダー
topic = session.query(Topic).filter(Topic.id == topic_id).one_or_none()

# 禁止: 文字列フォーマット
# topic = session.query(Topic).filter(f"id = {topic_id}").one_or_none()
```

### 10. 暗号化通信(本番)

**開発**: http://localhost:8000
**本番**: HTTPS 必須(reverse proxy で SSL/TLS)

例(nginx):
```nginx
server {
    listen 443 ssl http2;
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    proxy_pass http://localhost:8000;
}
```

## 本番環境への移行チェックリスト

### セキュリティ設定

- [ ] `SECRET_ENCRYPTION_KEY` 設定・安全に生成(dev から流用禁止)
- [ ] `ANTHROPIC_API_KEY` 設定(本番キー)
- [ ] `YOUTUBE_OAUTH_CLIENT_SECRET` 設定(本番クレデンシャル)
- [ ] `.env` ファイル`.gitignore` 確認・コミット対象外
- [ ] `.env` を source control 外で管理(Vault / AWS Secrets Manager など)

### 暗号化・認証

- [ ] SECRET_ENCRYPTION_KEY の強度確認(最小24文字以上)
- [ ] OAuthToken table のすべての refresh_token が暗号化を確認
  ```sql
  SELECT COUNT(*) FROM oauth_token WHERE refresh_token NOT LIKE 'gAAAAAB%';
  ```
  結果が 0 なら全て暗号化済み。

- [ ] CSRF トークン生成ロジック有効化(Starlette middleware 有効確認)

### API・通信

- [ ] HTTPS 設定(reverse proxy 経由)
- [ ] Content-Security-Policy header 設定
- [ ] Rate limiting 導入(オプション)

### 管理画面

- [ ] `ADMIN_USERNAME` / `ADMIN_PASSWORD` を本番用の強力な値へ設定(D-019: HTTP Basic認証。
  未設定かつ `APP_ENV` が development/test 以外なら fail-closed で全リクエスト401になる)
- [ ] CSRF トークン全フォーム検証済み

### DB

- [ ] PostgreSQL `postgres` ユーザパスワード変更(.env に強力なパスワード設定)
  ```env
  DATABASE_URL=postgresql+psycopg://postgres:SecurePassword123@localhost:5432/auto_movie_system
  ```

- [ ] SSL/TLS で接続(オプション)
  ```env
  DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db?sslmode=require
  ```

- [ ] 定期バックアップ自動化(cron / systemd timer)

### ログ・監視

- [ ] ログを外部ストレージ集約(ELK / CloudWatch)
- [ ] アラート設定(予算80%、エラーレート)
- [ ] audit log 確認可能(Approval テーブル検査)

### プロバイダー設定

- [ ] `LLM_PROVIDER=anthropic` (Fake から切り替え)
- [ ] `YOUTUBE_PROVIDER=real` (Fake から切り替え)
- [ ] `TTS_PROVIDER=generic_command` またはサービス(Fake から切り替え)

### テスト

- [ ] 全テスト pass (unit/integration/e2e)
- [ ] security-check pass
  ```powershell
  ./scripts/dev.ps1 security-check
  ```

### 展開

- [ ] Docker image セキュリティスキャン(docker scan)
- [ ] 環境変数注入確認
- [ ] startup スクリプト(alembic upgrade head, seed 等)動作確認

## 現状の制限・将来対応

### 管理画面認証

**現状**: `app/core/auth.py` の `require_admin` dependency により、web/api 全ルーター
(`/health` を除く)に HTTP Basic 認証を適用済み(D-019)。`ADMIN_PASSWORD` 未設定時は
`APP_ENV` が development/test の場合のみバイパスする(それ以外は fail-closed で401)。

**将来対応**:
1. OAuth 2.0 / OIDC 連携(Google/GitHub など)によるユーザー個別識別・ロール分離
2. 複数管理者運用時のセッション管理・監査ログの操作者別集計
3. Rate limiting / ブルートフォース対策(下記参照)

### 詳細な監査ログ

**現状**: Approval のみ(承認/却下)。

**将来拡張**:
- Topic 作成・編集・削除
- 予算変更
- Settings 変更
- admin only API アクセス

### Rate Limiting

**現状**: 未実装。

**本番対応**:
- FastAPI の SlowAPI / Starlette middleware
- API key ベースの quota 制御

### サブスクリプション・支払い

**現状**: 実装なし(MVP スコープ外)。

## 関連ドキュメント

- **[docs/operations.md](operations.md)** - バックアップ・復旧
- **[docs/content-policy.md](content-policy.md)** - 無断転載禁止等
- **[DECISIONS.md](../DECISIONS.md)** - ADR-0007(Fernet暗号化決定)
