# YouTube自動投稿・レビュー・成長改善システム

YouTubeチャンネル向けの**企画→台本→動画生成→レビュー→投稿→分析→フィードバック**の一気通貫MVP。
企画候補から公開まで**全処理が自動化**でき、人間承認ゲートで品質確保。コスト管理・監査ログ完備。

## 主要機能

- **企画・スコアリング**: CSV/手動入力から Demand/Revenue など6因子で自動スコア計算
- **台本生成**: LLMで企画から台本初稿を自動生成、構造化検査で修復リトライ
- **動画生成**: TTS字幕+FFmpegで H.264/AAC MP4を自動レンダリング、ffprobe検査
- **自動レビュー**: 機械検査(映像フォーマット・音量・沈黙)＋ LLMコンテンツ検査
- **人間承認**: fail-closed デフォルト。管理画面で承認/却下、監査ログ記録
- **YouTube投稿**: OAuth 2.0 resumable upload、private→public の公開ゲート(6条件)、reconcile による重複防止
- **分析・フィードバック**: 日次指標同期、コメント差分同期、分類・Insight生成で次回企画候補へ
- **冪等性**: すべてのジョブは `idempotency_key` + checksum で再実行時の重複投稿を防止
- **コスト管理**: AI(LLM/TTS)呼び出しを UsageRecord に記録、日次/月次予算で 80%警告・100%停止

## 前提

- **Python 3.12** (via `uv`)
- **FFmpeg 8.0+** (winget で `Gyan.FFmpeg` または手動導入)
  - Windows: `winget install Gyan.FFmpeg`
  - Linux/Mac: `brew install ffmpeg` など
- **Docker Desktop** (docker compose 環境でのみ必要)
- **PostgreSQL 16** (docker compose に含まれる。ローカル検証は SQLite でも可)

## クイックスタート (Windows PowerShell)

### 1. リポジトリクローン

```powershell
git clone <repository-url>
cd AutoMovieSystem
```

### 2. Python 環境セットアップ

```powershell
./scripts/dev.ps1 setup
```

`uv sync` で依存関係をインストール。`.env` がなければ `.env.example` をコピーし、必要に応じて編集。

### 3. データベース初期化

```powershell
./scripts/dev.ps1 migrate
./scripts/dev.ps1 seed
```

- `migrate`: Alembic で スキーマ作成
- `seed`: デモチャンネル＋サンプル企画CSV を取り込み

### 4. 全フロー自動実行 (Fake プロバイダー)

```powershell
./scripts/dev.ps1 demo
```

引数なし。SQLite + eager Celery + 全Fakeプロバイダーで企画→スコア→台本→動画生成→レビュー→承認→アップロード→指標同期→Insight生成を**一気に完走**。
結果サマリーを表示。

### 5. 管理画面起動

```powershell
uv run uvicorn app.main:app --reload
```

ブラウザで **http://localhost:8000/dashboard** を開く。
Jinja2+HTMX による管理画面で企画・台本・レビュー・公開状況を監視。

管理画面/APIはHTTP Basic認証で保護されている(D-019)。`APP_ENV=development`かつ
`ADMIN_PASSWORD`未設定のローカル開発時は認証を求めずアクセス可能。本番相当の
`APP_ENV`では`ADMIN_USERNAME`/`ADMIN_PASSWORD`(`.env`)の設定が必須(未設定は常に401)。

### 6. 日常の開発・テスト

```powershell
# ユニットテスト(SQLite + eager Celery)
./scripts/dev.ps1 test-unit

# 統合テスト(PostgreSQL + Redis)— CIまたは docker compose 環境で実行
./scripts/dev.ps1 test-integration

# E2E テスト(企画→Insight 2回実行、重複ゼロ検証)
./scripts/dev.ps1 test-e2e

# コード品質チェック
./scripts/dev.ps1 lint
./scripts/dev.ps1 typecheck

# 生成物クリア
./scripts/dev.ps1 clean-generated
```

## Docker Compose で起動 (本番型)

### 前提条件

- Docker Desktop インストール・起動済み
- `.env` ファイルが存在し、`CELERY_TASK_ALWAYS_EAGER=false` に設定

### 起動

```powershell
# イメージビルド & サービス起動(app/worker/beat/postgres/redis)
./scripts/dev.ps1 up

# スキーマ作成・シード
./scripts/dev.ps1 migrate
./scripts/dev.ps1 seed

# ブラウザで http://localhost:8000/dashboard にアクセス
```

worker/beat はコンテナ内で自動起動。

### 停止

```powershell
./scripts/dev.ps1 down
```

## テスト戦略

| テスト | DB | Celery | API呼び出し | 実行環境 |
|---|---|---|---|---|
| **unit** | SQLite in-memory | eager | Fake providers | `pytest tests/unit` |
| **integration** | PostgreSQL (docker) | Redis | Fake providers | `pytest -m integration` |
| **e2e** | ローカルDB | eager | Fake providers | 企画→Insight 2回、重複ゼロ検証 |

`-m integration` マーカーのテストは docker compose 環境または CI のサービスコンテナで実行。

## 実 YouTube 接続

Fakeプロバイダー(デフォルト)では全フロー完走しますが、実YouTubeへのアップロードには:

1. `.env` に設定:
   ```
   YOUTUBE_PROVIDER=real
   YOUTUBE_OAUTH_CLIENT_ID=<取得した値>
   YOUTUBE_OAUTH_CLIENT_SECRET=<取得した値>
   SECRET_ENCRYPTION_KEY=<Fernetキー>
   ```

2. スクリプト実行でリフレッシュトークン取得:
   ```
   uv run python scripts/youtube_oauth_setup.py --client-secret path/to/client_secret_xxxx.json
   ```

詳細は **[docs/youtube-oauth.md](docs/youtube-oauth.md)** 参照。

## 自動公開の有効化

fail-closed がデフォルト:
- `YOUTUBE_DEFAULT_PRIVACY_STATUS=private` (常にプライベート)
- `AUTO_PUBLISH_ENABLED=false` (手動承認後も公開しない)
- `REQUIRE_HUMAN_APPROVAL=true` (人間承認必須)

自動公開するには:

```
AUTO_PUBLISH_ENABLED=true
```

ただし以下の **6条件をすべて満たす**場合のみ実行:

1. 自動レビュー合格(blocking findings = 0)
2. Approval レコード存在(人間承認済み)
3. アップロードファイルのチェックサム一致
4. メタデータ確定
5. 重複 `youtube_video_id` なし(同じ動画の二重投稿防止)
6. 有効な OAuth 認証

1つでも欠ければ `private` のまま保留。

## ディレクトリ構成

```
AutoMovieSystem/
├── app/
│   ├── models/              14エンティティのSQLAlchemyモデル
│   ├── services/            ドメインロジック(topics/scripts/media/reviews/publishing/analytics)
│   ├── providers/           外部API抽象化(llm/tts/youtube)— Protocol + Fake実装
│   ├── repositories/        DB アクセスレイヤー
│   ├── workers/             Celery タスク(薄いラッパー)
│   ├── web/                 管理画面(Jinja2+HTMX)
│   ├── api/                 JSON API
│   ├── core/                config / logging / crypto / subprocess
│   ├── db/                  SQLAlchemy/Alembic セッション・マイグレーション
│   ├── templates/           Jinja2 テンプレート
│   └── static/              CSS / JS / 画像
├── tests/
│   ├── unit/                SQLite + eager
│   ├── integration/         PostgreSQL + Redis
│   ├── contract/            Fake と実装の Protocol 契約検証
│   └── e2e/                 企画→Insight 全フロー
├── scripts/
│   ├── dev.ps1              Windows コマンド(Makefile代替)
│   ├── demo.py              全フロー自動実行
│   ├── seed.py              デモデータ取り込み
│   └── youtube_oauth_setup.py   OAuth トークン取得
├── docs/
│   ├── architecture.md      全体設計
│   ├── setup.md             開発環境セットアップ詳細
│   ├── operations.md        日常運用・障害復旧
│   ├── security.md          セキュリティ対策
│   ├── content-policy.md    コンテンツポリシー
│   ├── cost-control.md      コスト管理
│   ├── youtube-oauth.md     YouTube OAuth 手順
│   └── adr/                 設計判断記録(ADR-0001〜0007)
├── alembic/                 DB マイグレーション
├── generated/               生成物(動画・音声)— git 管理外
├── Makefile                 Linux/Mac/CI コマンド
├── docker-compose.yml       本番型環境定義
├── pyproject.toml           uv 依存管理
├── .env.example             設定テンプレート
├── CLAUDE.md                プロジェクト指示書
├── TASKS.md                 進行状況
└── DECISIONS.md             採用決定事項

sample_data/
└── topics_sample.csv        デモ企画データ
```

## ドキュメント

| ドキュメント | 内容 |
|---|---|
| **[docs/architecture.md](docs/architecture.md)** | 全体設計・データフロー・状態機械・冪等性 |
| **[docs/setup.md](docs/setup.md)** | 開発環境詳細(uv/FFmpeg/PostgreSQL/.env) |
| **[docs/operations.md](docs/operations.md)** | 日常運用・ジョブ管理・障害復旧 |
| **[docs/security.md](docs/security.md)** | セキュリティ実装・秘密管理・監査 |
| **[docs/content-policy.md](docs/content-policy.md)** | コンテンツポリシー・自動公開条件 |
| **[docs/cost-control.md](docs/cost-control.md)** | AI予算・UsageRecord・料金表 |
| **[docs/youtube-oauth.md](docs/youtube-oauth.md)** | YouTube OAuth 取得手順 |
| **[DECISIONS.md](DECISIONS.md)** | 採用決定事項(D-001〜D-018) |
| **[TASKS.md](TASKS.md)** | Phase 進行状況 |

## コマンドリファレンス

Windows PowerShell:

```powershell
./scripts/dev.ps1 setup             # uv sync
./scripts/dev.ps1 up                # docker compose up
./scripts/dev.ps1 down              # docker compose down
./scripts/dev.ps1 migrate           # alembic upgrade head
./scripts/dev.ps1 seed              # seed data (samples.csv + demo channel)
./scripts/dev.ps1 demo              # full pipeline run (Fake providers)
./scripts/dev.ps1 lint              # ruff check
./scripts/dev.ps1 typecheck         # mypy check
./scripts/dev.ps1 test              # pytest (all)
./scripts/dev.ps1 test-unit         # pytest tests/unit
./scripts/dev.ps1 test-integration  # pytest -m integration
./scripts/dev.ps1 test-e2e          # pytest -m e2e
./scripts/dev.ps1 security-check    # pip check + pip-audit
./scripts/dev.ps1 clean-generated   # Remove generated/ (videos/audio)
```

Linux/Mac (Makefile):

```bash
make setup
make up / down
make migrate / seed / demo
make test / test-unit / test-integration / test-e2e
make lint / typecheck
make security-check / clean-generated
```

## トラブルシューティング

### Q: `FFmpeg not found` エラーが出る

A: `FFMPEG_PATH` が未設定の場合、config が PATH → WinGet展開先の順で自動解決します。
   手動指定: `.env` に `FFMPEG_PATH=C:\path\to\ffmpeg.exe` を設定。

### Q: Docker compose で接続できない

A: `docker compose up` 後、ヘルスチェック完了を待つ(5秒×10回)。
   `docker logs` で詳細確認。

### Q: 動画生成が遅い

A: ffmpeg レンダリング中。デフォルト 1920x1080 H.264 で 数秒〜数十秒。
   `scripts/demo.py` は Celery eager モードのため同期実行。

### Q: 予算が 100% に達した

A: `/usage` 画面で現在の利用状況確認。月初リセット待つか、 `.env` で
   `MONTHLY_AI_BUDGET_MICRO_USD` を増額(要アプリ再起動)。

## ライセンス & 引継ぎ

このプロジェクトは MaintainerName(masaxeon@gmail.com) が保守。
詳細は DECISIONS.md / TASKS.md / docs/adr/ を参照。
