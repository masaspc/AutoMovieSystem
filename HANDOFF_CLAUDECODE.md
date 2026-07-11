# ClaudeCode 引き継ぎメモ

## 最新更新 (2026-07-12, Codex)

Phase 1の運用安定化はcommit `ae00d19`で完了。Phase 2の長尺・台本自由度対応を引き継ぎ、
未完了差分のレビューと補完を実施した。

- `ProductionSettings`と`VideoProject.production_settings` JSON列、Alembic migrationを追加
- short/3分/5分/8分/custom、7種類のscript template、トーン・掛け合い比率へ対応
- 目標尺・文字数・セクション数・時間配分を`script_v3`プロンプトへ反映
- 音声化対象sectionsの文字数から推定し、範囲外なら最大2回LLM修復
- 修復もUsageRecord・予算・キャッシュ対象。Script使用量は全試行合計
- Evidence IDが欠落または追加された修復結果を破棄
- ProductionSettings checksumを冪等キーへ含め、設定別Scriptを正しく再利用
- JSON APIからProductionSettingsを指定可能。Script manifestからVideoProjectへ引き継ぐ
- TTS再実行時にレビュー期待尺を実測値で必ず更新
- README、operations、architecture、TASKS、DECISIONS(D-022)を更新

検証結果:

```powershell
uv run ruff check .
# All checks passed!
uv run mypy app
# Success: no issues found in 135 source files
uv run pytest -q
# 373 passed, 1 skipped (FFmpeg未検出), 1 warning
```

SQLiteのAlembic `upgrade head -> downgrade 8ddd9daa528c -> upgrade head`も成功。
残課題はProductionSettingsの管理画面フォームと、`speaking_rate`の実TTSエンジンへの反映。

## 最新更新 (2026-07-11, Codex)

対象HEAD: `f252b4c` (グロース機能まで実装済み)。この追記を含む作業ツリーには未コミットの
レビュー是正があります。次の実装者は、まず `git diff` を確認してからコミットしてください。

### 今回完了したレビュー是正

- LLM gateway が `LLM_PROVIDER_LOW/MID/HIGH` の実際の設定を解決するよう修正。
  `local` 選択時はローカルモデル名で使用量を記録し、課金ゼロとして予算予約もゼロになる。
- `get_youtube_provider()` が同一アプリプロセス内では共有Fakeストアを使うよう修正。
  別管理画面リクエストで、Fake投稿後の公開予約が `video not found` にならない。
- production相当では FastAPI の `/docs`、`/redoc`、`/openapi.json` を無効化。
- Docker Compose の app/worker/beat は `APP_ENV=production` を強制。
  `.env` の `ADMIN_PASSWORD` が空の場合、認証バイパスではなく全管理操作が401となる。
- ベンチマークURLを YouTube の `http(s)` URL に限定し、`javascript:` 等を拒否。
- CI security job の `continue-on-error` を除去し、`pip-audit` の失敗をCI失敗に変更。

### 追加した回帰テスト

- ポリシー別 `local` ルーティングで予算1 micro-USDでも実行・記録できること。
- Factoryを別々に呼んでもFake YouTubeの予約ができること。
- productionで標準OpenAPIドキュメントが404になること。
- 危険なベンチマークURLが保存されないこと。

### 検証済み

```powershell
uv run pytest tests/unit/test_llm_gateway.py tests/unit/test_youtube_fake_provider.py tests/unit/test_auth.py tests/unit/test_growth.py -q
# 33 passed
uv run ruff check app tests
uv run ruff format --check app tests
uv run mypy app
docker compose config --quiet
# uv run pytest -q
# 299 passed, 1 skipped
```

### 残る注意点

- Fake YouTubeの共有ストアはプロセス内に限定される。実運用はReal YouTube APIが正本であり問題ないが、複数workerをまたぐFake状態の永続シミュレーションは対象外。
- `datetime.utcnow()` とTestClientの非推奨警告は既存分が残る。
- 全件 `pytest -q`: 299 passed, 1 skipped (2026-07-11)。

## 2026-07-05 時点の履歴

作成日: 2026-07-05
作成者: Codex
対象HEAD: `658b685 Phase 5: YouTube投稿(OAuth/冪等アップロード/Fake YouTube/reconcile)`

## 現在の完成度目安

全体完成度は約 76% と見ています。

- Phase -1〜5 は実装・コミット済みの状態から継続。
- 今回、Phase 6 として「分析指標同期」「コメント同期/分類」「コメント/指標由来Insight」「コメント由来Topic候補生成」「API」「Celeryタスク」を追加。
- Phase 6 は `TASKS.md` 上で完了扱いに更新済み。
- Phase 7, Phase 8 は未着手です。

## 今回の主な変更

### 1. 途中実装の安定化

ClaudeCode 停止時点の未コミット差分を壊さない形で、既存の失敗永続化・冪等実行まわりを検証し、足りない部分を補いました。

- `JobRun` の `in_progress`/lease 扱いを前提にしたタスクの失敗永続化を確認。
- worker タスク失敗時、トランザクション rollback 後にも失敗状態を別セッションで保存する流れを補強。
- upload 失敗時に `Publication(upload_status="failed", last_error=...)` が残るように補強。
- API 側で `JobInProgressError` を HTTP 409 にマップ。
- `Asset.role` 追加とメディアパイプラインのAsset upsert系テストを確認。

関連ファイル:

- `app/services/jobs.py`
- `app/services/publishing/uploader.py`
- `app/workers/tasks/media.py`
- `app/workers/tasks/publishing.py`
- `app/workers/tasks/reviews.py`
- `app/workers/tasks/scripts.py`
- `app/api/publications.py`
- `app/api/scripts.py`
- `app/models/asset.py`
- `migrations/versions/8f38a5e7cce7_add_asset_role_column.py`
- `tests/unit/test_worker_task_failure_persistence.py`
- `tests/unit/test_media_pipeline_asset_upsert.py`

### 2. Phase 6 MVP: 分析・コメント・フィードバック

以下を追加しました。

- `VideoMetricDaily`
  - `publication_id + metric_date` で日次指標を一意化。
  - views/likes/comments_count を保存。
- `Comment`
  - YouTubeコメントIDで一意化。
  - author はハッシュ保存。
  - ルールベースでカテゴリ分類。
- `Insight`
  - コメント要望などから改善/企画候補を保存。
  - `source_ref` を持たせ、同一Publication内の複数Insightを安全に重複排除。
  - `human_review_reason` を保存。
- Alembic migration
  - `a1c9d4e5f607_add_metrics_comments_and_insights.py`
- 分析同期サービス
  - `sync_video_metrics(...)`
- コメント同期/分類サービス
  - `sync_comments(...)`
  - `classify_text(...)`
  - `classify_comment(...)`
  - 取得結果から消えたコメントは `moderation_status="deleted"` に更新。
- フィードバックInsight生成
  - `generate_comment_insights(...)`
  - `generate_metric_insights(...)`
  - `generate_publication_insights(...)`
  - 同種の次回企画要望・質問・比較要望は `Insight` と `Topic(source_type="comment")` を作成/更新。
  - 同一エラー報告は補足候補Insightを作成/更新。
  - CTR/維持率/登録増/コメント率の基本ルールから改善Insightを作成。
- Phase 6 API
  - `GET /api/publications/{id}/metrics`
  - `POST /api/publications/{id}/sync-metrics`
  - `GET /api/publications/{id}/comments`
  - `POST /api/publications/{id}/sync-comments`
  - `GET /api/publications/{id}/insights`
  - `POST /api/publications/{id}/generate-insights`
  - `POST /api/publications/{id}/sync-feedback`
  - `POST /api/analytics/sync-completed`
- Phase 6 Celeryタスク
  - `analytics.sync_metrics`
  - `analytics.sync_comments`
  - `analytics.generate_insights`
  - `analytics.sync_feedback`
  - `analytics.sync_completed_feedback`

追加ファイル:

- `app/models/video_metric_daily.py`
- `app/models/comment.py`
- `app/models/insight.py`
- `app/services/analytics/__init__.py`
- `app/services/analytics/sync.py`
- `app/services/comments/__init__.py`
- `app/services/comments/classifier.py`
- `app/services/comments/sync.py`
- `app/services/feedback/__init__.py`
- `app/services/feedback/insights.py`
- `app/services/feedback/sync.py`
- `app/schemas/analytics.py`
- `app/api/analytics.py`
- `app/workers/tasks/analytics.py`
- `migrations/versions/a1c9d4e5f607_add_metrics_comments_and_insights.py`
- `tests/unit/test_phase6_sync_and_insights.py`
- `tests/unit/test_analytics_api.py`

## 検証結果

以下は 2026-07-05 時点で成功済みです。

```powershell
uv run ruff check .
# All checks passed!
```

```powershell
uv run mypy app
# Success: no issues found in 106 source files
```

```powershell
$tmp = Join-Path $env:TEMP ('automovie-migration-' + [guid]::NewGuid().ToString() + '.db')
$env:DATABASE_URL = 'sqlite:///' + ($tmp -replace '\\','/')
uv run alembic upgrade head
# ce546f0ebda2 -> ... -> 8f38a5e7cce7 -> a1c9d4e5f607 まで成功
```

```powershell
uv run pytest -q
# 240 passed, 1 skipped
```

警告は残っています。

- `datetime.utcnow()` の deprecation warning が多数。
- FastAPI/TestClient 経由で Starlette の `httpx` deprecation warning。

今回の作業では既存挙動を優先して警告対応はしていません。

## 注意点

- `HANDOFF_CLAUDECODE.md` を含め、今回の変更は未コミットです。
- `TASKS.md` の Phase 6 は `[x]` に更新済みです。
- コメント分類はMVPのルールベースです。LLM分類、スパム/炎上/返信優先度の精緻化は未実装です。
- 実YouTube Analytics APIの詳細指標ではなく、既存の `YouTubeProvider` 抽象の `get_video_statistics`/`list_comments` と保存済み拡張指標を使ったMVPです。
- Docker Desktop/compose 実起動検証は未実施です。

## 次にやるとよいこと

1. 現在の未コミット差分を確認して、Phase 5補強分とPhase 6分を適切な粒度でコミット。
2. Insight生成の仕様精緻化。
   - 複数トピック候補をどう扱うか。
   - Topic候補のレビュー/承認フロー。
3. Phase 7 管理画面。
   - 投稿状況、承認、分析、コメント、改善候補を見られる画面。
4. Phase 8 総合検証。
   - E2E x2。
   - 冪等性。
   - セキュリティ。
   - Docker/compose。
   - 最終レポート。
