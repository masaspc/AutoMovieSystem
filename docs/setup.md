# 開発環境セットアップ詳細

ローカルマシンで開発・検証するための完全セットアップガイド。

## 前提条件

- **Windows 11** (または macOS / Linux)
- **uv** または **Python 3.12** (uv で管理)
- **FFmpeg 8.0+**
- **Git**

## Step 1: Python 3.12 の導入 (uv 経由)

### Windows (推奨: uv による自動管理)

```powershell
# uv をインストール(Rustup 必要)
# https://docs.astral.sh/uv/getting-started/ 参照

# リポジトリディレクトリで
./scripts/dev.ps1 setup
```

これで `uv sync` が実行され、`pyproject.toml` 指定の Python 3.12.13 と全依存パッケージがインストール。
このコマンドは `.env` を作成しない。次のStep 3で明示的に作成する。

### macOS / Linux

```bash
# uv をインストール
curl https://astral.sh/uv/install.sh | sh

# リポジトリで
make setup
# または
uv sync
```

## Step 2: FFmpeg のインストール

### Windows (winget)

```powershell
winget install Gyan.FFmpeg
```

インストール後、PowerShell をリスタートするか、PATH を再読み込み:

```powershell
$env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User")
ffmpeg -version  # 動作確認
```

自動解決が失敗する場合は `.env` に明示:

```env
FFMPEG_PATH=C:\Users\YourName\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_<version>\ffmpeg.exe
FFPROBE_PATH=C:\Users\YourName\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_<version>\ffprobe.exe
```

### macOS

```bash
brew install ffmpeg
ffmpeg -version  # 動作確認
```

### Linux (Ubuntu/Debian)

```bash
sudo apt-get update
sudo apt-get install ffmpeg
ffmpeg -version
```

## Step 3: 環境変数ファイル (.env) 作成

`.env.example` をコピー:

```powershell
# Windows
Copy-Item .env.example .env

# macOS/Linux
cp .env.example .env
```

### 最小設定 (Fake プロバイダーのみ)

`.env` の以下の項目を確認:

```env
APP_ENV=development

# ローカル SQLite(Docker不要)
DATABASE_URL=sqlite:///./local.db

# Celery eager モード(開発/テスト)
CELERY_TASK_ALWAYS_EAGER=true

# Fakeプロバイダーで全機能デモ可能(キー不要)
LLM_PROVIDER=fake
TTS_PROVIDER=fake
YOUTUBE_PROVIDER=fake

# 秘密鍵(この段階では空でもOK。実YouTubeなら必須)
SECRET_ENCRYPTION_KEY=
```

保存。これで `make demo` / `./scripts/dev.ps1 demo` で全フロー実行可能。

`APP_ENV=development`のままなら管理画面/APIへのHTTP Basic認証(D-019)は
`ADMIN_PASSWORD`未設定でもバイパスされる。本番相当の`APP_ENV`にする場合は
`ADMIN_USERNAME`/`ADMIN_PASSWORD`を必ず設定すること(未設定は常に401)。
`docker compose` は `APP_ENV=production` を強制するため、起動前に必ず
`ADMIN_PASSWORD` を設定すること。

## Step 4: SECRET_ENCRYPTION_KEY の生成(推奨・必須)

Fernet 対称鍵を生成:

```powershell
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

出力例:
```
Ky_4HvL5z...Wc=
```

`.env` に設定:

```env
SECRET_ENCRYPTION_KEY=Ky_4HvL5z...Wc=
```

**必須条件**:
- 実YouTubeプロバイダー使用時
- 本番環境(絶対)

**開発時(Fake のみ)**: 空ままでも動作。ただし将来 OAuth トークン暗号化を使うなら生成推奨。

## Step 5: データベース初期化

### SQLite 環境 (推奨・ローカル検証)

```powershell
./scripts/dev.ps1 migrate
```

`local.db` が自動作成。

```powershell
./scripts/dev.ps1 seed
```

デモチャンネル + サンプル企画 CSV を取り込み。複数実行しても冪等(重複なし)。

### PostgreSQL 環境 (docker compose)

`.env` を修正:

```env
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/auto_movie_system
CELERY_TASK_ALWAYS_EAGER=false
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<強力なパスワード>
SECRET_ENCRYPTION_KEY=<Fernetキー>
```

起動:

```powershell
./scripts/dev.ps1 up
./scripts/dev.ps1 migrate
./scripts/dev.ps1 seed
```

compose が postgres / redis / app / worker / beat を起動(ヘルスチェック付き)。

## Step 6: 動作確認

### 全フロー実行 (Fake providers)

```powershell
./scripts/dev.ps1 demo
```

- 既定では安全のため `.env` の `DATABASE_URL` を使わず、SQLiteの `demo.db` を使用
- Celery eager モードで企画スコアリング → 台本生成 → 動画レンダリング → レビュー → アップロード(Fake) → 指標同期 → Insight生成 を一気に実行
- 結果サマリーを表示(Topic数、Script ID、動画パス、ステータス等)

管理画面が接続するDB(例: `local.db` またはComposeのPostgreSQL)にデモ結果を残す場合だけ、
明示的に既存の `DATABASE_URL` を使う:

```powershell
$env:DEMO_USE_CURRENT_DB="1"
./scripts/dev.ps1 demo
Remove-Item Env:DEMO_USE_CURRENT_DB
```

**期待結果**: VideoProject status = `FEEDBACK_GENERATED` / approval有 / publication有 / metrics_synced > 0

### 管理画面起動

```powershell
uv run uvicorn app.main:app --reload
```

ブラウザで http://localhost:8000/dashboard → ダッシュボード表示確認。
Compose環境では `APP_ENV=production` のため、`.env` の `ADMIN_USERNAME` /
`ADMIN_PASSWORD` でHTTP Basic認証を行う。

### テスト実行

```powershell
./scripts/dev.ps1 test-unit      # SQLite + eager (最速)
./scripts/dev.ps1 test-integration # PG + Redis (docker 必須)
./scripts/dev.ps1 test-e2e        # 企画→Insight 2回、重複ゼロ検証
```

## Celery eager モード切り替え

### 開発時 (eager = true)

```env
CELERY_TASK_ALWAYS_EAGER=true
```

Redis なし。ジョブが同期実行。デバッグ・テスト向け。

### 本番型 (eager = false)

```env
CELERY_TASK_ALWAYS_EAGER=false
REDIS_URL=redis://localhost:6379/0
```

worker/beat が別プロセスで動作。docker compose が自動起動。

## トラブルシューティング

### Q: `uv` コマンドが見つからない

A: uv をインストール。`https://docs.astral.sh/uv/installation/`

### Q: FFmpeg PATH が解決されない

A: `.env` に明示的に設定。WinGet 展開先は通常
   `C:\Users\<User>\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_<ver>`

### Q: SQLite ファイルがロックされている

A: 別のプロセスが使用中。process explorer で確認・終了するか、 `.env` で異なるパスを指定。

### Q: `alembic upgrade head` が失敗

A: 既存スキーマと不整合。 `local.db` を削除して再実行:
   ```powershell
   rm local.db
   ./scripts/dev.ps1 migrate
   ```

### Q: 動画ファイルが `generated/` に作成されない

A: FFmpeg がパスで見つからない可能性。 `ffmpeg -version` で確認。
   または `.env` で `FFMPEG_PATH` を明示。

### Q: Docker compose で `service_healthy` がタイムアウト

A: postgres/redis のヘルスチェック完了待ち(デフォルト 50秒)。
   `docker logs <container>` で詳細確認。

## 次ステップ

- **README.md** でクイックスタート確認
- **docs/operations.md** で日常運用・ジョブ管理を学習
- **docs/youtube-oauth.md** で実YouTube接続(オプション)
- **DECISIONS.md** で設計判断背景を理解
