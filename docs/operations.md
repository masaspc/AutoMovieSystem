# 日常運用・ジョブ管理・障害復旧

本番環境での日々の操作・トラブル対応手順。

## 管理画面の主なページ

http://localhost:8000/dashboard にアクセス(Jinja2+HTMX):

| ページ | URL | 役割 |
|---|---|---|
| **ダッシュボード** | `/dashboard` | 全体サマリー(Topic数・最新Project・指標概観) |
| **企画(Topics)** | `/topics` | Topic 一覧・新規追加・スコア確認 |
| **動画プロジェクト** | `/video-projects` | VideoProject 状態追跡・再実行 |
| **レビュー(Reviews)** | `/reviews` | 自動レビュー結果・blocking finding 確認 |
| **承認(Approvals)** | `/approvals` | 人間承認・却下(CSRF+監査ログ) |
| **公開(Publications)** | `/publications` | YouTube アップロード状況・スケジュール状態 |
| **コメント(Comments)** | `/comments` | YouTube コメント一覧・分類(QUESTION等) |
| **インサイト(Insights)** | `/insights` | 自動生成 Insight・次回企画候補 |
| **トレンド(Trends)** | `/trends` | RSS見出しの確認・ニュース解説Shortの即動画化 |
| **ジョブ(Jobs)** | `/jobs` | 実行状況・失敗ジョブ詳細・再実行操作 |
| **利用状況(Usage)** | `/usage` | AI予算 (daily/monthly)・警告表示 |
| **設定(Settings)** | `/settings` | 環境変数確認・予算変更 |
| **ヘルス** | `/health` | DB/Redis 接続確認 |

## 日常フロー: 企画→投稿

### シリーズ講座の場合

`/series`で全体方針を作成し、カリキュラム生成→各Episode編集→全体承認の順で進める。
制作開始するとEpisode固有のTopic/VideoProjectを冪等作成し、シリーズ文脈付き台本生成を
Celeryへ投入する。各話の新規概念と「まだ扱わない概念」がプロンプト制約になる。

制作開始前はEpisodeの編集・上下移動が可能。Topic作成済みEpisodeは固定され、1本でも
制作開始済みの場合はカリキュラム全体の再生成を拒否する。大幅な方向転換は新シリーズまたは
カリキュラムversionを分けて行う。

### 1. 企画投入

**手動入力**:
1. `/topics` → "新規Topic"
2. タイトル・説明・ターゲット視聴者を入力
3. 保存 → `TOPIC_CREATED` → ジョブキュー投入

**CSV インポート**:
```powershell
# sample_data/topics_sample.csv のフォーマット
uv run python scripts/seed.py
```
自動で `デモチャンネル` へ取り込み(重複なし)。

### 2. スコアリング (自動)

企画投入直後、Celery worker が `score_topic` 実行:
- Demand(需要) / Expertise / Originality / Revenue / Freshness / ProductionCost の6因子を計算
- Topic status → `TOPIC_SCORED`
- 総スコアは `/topics` で確認

### 3. 台本生成 (半自動)

- status `RESEARCH_READY` → `generate_script` 実行(LLM)
- Fake provider では deterministic 出力。実 Anthropic ならキャッシュ効いて節約
- 構造化出力失敗時は自動リトライ(最大1回)
- 制作設定を指定した場合、目標尺・文字数・セクション数・構成・トーンをプロンプトへ反映
- 推定尺がプリセットの範囲外なら最大2回まで増補または短縮する
- 修復呼び出しもUsageRecord・予算制御の対象。Evidence IDが増減した修復結果は破棄する
- status → `SCRIPT_REVIEWED`

尺プリセットは`short`（45秒）、`standard_3min`、`standard_5min`、`standard_8min`、
`custom`。企画詳細の「動画の制作設定」または
`POST /api/topics/{topic_id}/generate-script`のJSON bodyで指定する。未指定は`short`。

`speaking_rate`は0.5〜2.0。VOICEVOXでは`speedScale`、Fake TTSでは生成尺、
Generic Commandではコマンドテンプレートの`{speed}`へ反映する。
動画レビューに使う`VideoProject.target_duration_seconds`は、TTS合成後に実測音声尺と
エンドカード尺から毎回再計算される。

台本生成後、素材準備前までは動画プロジェクト詳細からセクションを編集できる。
更新・追加・削除・上下移動は即時に新しいScript versionを作り、AI部分再生成はCeleryで
非同期実行する。素材生成後は音声・字幕との不整合を防ぐため編集不可。

### 4. 動画生成 (自動)

VideoProject 作成後、順次:
1. Assets 準備: 背景画像・エンドカード
2. TTS で字幕ナレーション音声合成
3. FFmpeg でレンダリング(1920x1080 H.264+AAC MP4)
4. ffprobe で検査(フォーマット・解像度・コーデック)
5. status → `VIDEO_RENDERED`

### 5. 自動レビュー (ゲート)

機械検査 + LLMコンテンツ検査:
- Machine: duration / 解像度 / 音量 / 沈黙検出
- Content: 高リスクキーワード検出 / 公開可否 LLMジャッジ
- blocking findings = 0 なら `AUTOMATED_REVIEW_PASSED`
- findings > 0 なら `/reviews` に保留、改修後 `/video-projects` で再実行

### 6. 人間承認 (必須)

`REQUIRE_HUMAN_APPROVAL=true` の場合:
1. `/approvals` で pending を確認
2. 台本・動画サムネイル確認
3. "承認" または "却下" を選択
4. 承認 → Approval レコード作成(監査ログ: decided_by記録)
5. status → `HUMAN_APPROVED`

却下の場合は status → `REJECTED` (終端。再投稿は新 VideoProject として投入)。

### 7. YouTube アップロード (自動)

status `HUMAN_APPROVED` → Celery worker が `upload_video`:

**2段階 idempotency**:
1. Publication レコード INSERT (`upload_status=STARTED`、`idempotency_key` UNIQUE制約)
2. resumable upload 実行(google-api-python-client)
3. youtube_video_id 記録 → `upload_status=COMPLETED`

**Reconcile (ADR-0005)**:
- 同一 idempotency_key で再試行時、description 内の `amx-idem:{key}` マーカーを検索
- 一致すれば既にアップロード済み(二重投稿防止)
- 不確実なら UPLOAD_FAILED で保留(fail-closed)

status → `UPLOADED_PRIVATE` (デフォルト privacy_status=private)

### 8. 公開スケジュール (条件付き)

`AUTO_PUBLISH_ENABLED=true` かつ **6条件すべて満たす** 場合のみ public 変更:

1. ✓ 自動レビュー合格 (blocking findings=0)
2. ✓ 人間承認あり (Approval存在)
3. ✓ チェックサム一致 (アップロード後も変更なし)
4. ✓ メタデータ確定
5. ✓ 重複 youtube_video_id なし(同じ動画は1回のみ投稿)
6. ✓ 有効な OAuth 認証

**デフォルト**: `AUTO_PUBLISH_ENABLED=false` → private のまま保留。
手動で `/publications` → "今すぐ公開" を選択して public に。

status → `SCHEDULED` / `PUBLISHED`

### 9. 指標・コメント収集 (定期自動)

published_at が non-NULL な Publication に対して:
- **指標同期** (`sync_video_metrics`): daily analytics(views, likes, shares)
- **コメント同期** (`sync_comments`): YouTube コメント差分取得・分類

分類: QUESTION / APPRECIATION / NEXT_TOPIC_REQUEST / CRITICISM / SPAM

### 10. フィードバック生成 (自動)

コメント・指標から Insight 生成:
- コメント分類・要約
- 次回企画候補の提案(例: "NEXT_TOPIC_REQUEST コメント多数 → 類似テーマ候補")
- `/insights` で確認

status → `FEEDBACK_GENERATED` (最終状態。新企画を投入して次サイクル)

## トレンド即応の運用

### 設定

既定は外部ネットワークへ接続しないFake Provider。RSSを使う場合は`.env`を次のように設定し、
設定を読み込むappを再起動する。

```dotenv
TREND_PROVIDER=rss
TREND_FEED_URLS=https://example.com/feed.xml,https://example.org/atom.xml
TREND_FETCH_LIMIT=20
```

`TREND_FEED_URLS`はRSS 2.0/Atomフィードのhttp(s) URLをカンマ区切りで指定する。
取り込むのは見出し、リンク、200字以内の要約、公開時刻だけで、記事本文は取得・保存しない。
テスト・デモでは`TREND_PROVIDER=fake`のまま運用する。

### 即動画化

1. ナビの「グロース」→`/trends`を開く。
2. 動画化先チャンネルを選び、「⚡ 即動画化(Short)」を押す。
3. 企画詳細の進捗バナーで一括制作を確認する。
4. 自動レビュー完了後は従来どおり内容を確認し、人間が承認・投稿する。

作成される動画は`short`、`news_commentary`、`serious`固定。出典URLはEvidenceへ保存される。
同じ記事URLはURLハッシュで同じTopicへ解決され、制作パイプラインの冪等性で重複を防ぐ。

### RSS障害時

到達不能、タイムアウト、不正XML、無効なURLはフィード単位で警告ログを残してスキップする。
正常なフィードの記事はそのまま表示される。全件空の場合は次を確認する。

1. `TREND_PROVIDER=rss`と`TREND_FEED_URLS`の綴り、URLのhttp(s)形式を確認する。
2. `docker compose logs app`で`trend_feed_*`警告と接続エラーを確認する。
3. フィードの到達性・XMLを修正し、`/trends`を再読み込みする。

## セルフレビュー改善ループの運用

### 手動実行と自動反映

投稿後指標が同期されたPublicationは、`/publications`または動画プロジェクト詳細の
「自己レビューを実行」から処理できる。最新の日次指標、場面別維持率Insight、コメント分類を
LLMで1回分析し、最大5件の`self_review` Insightを作る。LLM呼び出しは通常のUsageRecord・
予算管理対象である。

冪等キーは`self_review:{publication_id}:{指標日}`。同じ指標日の再操作ではLLMを重複実行せず、
新しい日の指標が入れば再レビューできる。保存された改善指示は、同じチャンネルの次回台本生成時に
「過去動画の振り返りからの改善指示」ブロックとして自動注入される。

### 日次beat

`feedback.run_daily_self_reviews`を86400秒ごとに実行し、実行時点のUTC前日分の指標がある投稿を
処理する。自動運用にはCelery workerとbeatの両方が必要。

```powershell
docker compose ps worker beat
docker compose logs beat worker
```

指標がない投稿は正常にスキップする。投稿1件の失敗で残りは停止せず、失敗はworkerログと
`/jobs`で確認する。予算超過やLLMエラーを解消後、対象投稿のボタンから手動で再実行する。

### 自動注入を止める

`/insights?insight_type=self_review`を開き、反映したくない改善提案の「削除」を実行する。
削除したInsightは以後の台本プロンプトへ注入されない。過去に生成済みの台本は変更されず、
新しい指標日のセルフレビューで追加されたInsightは必要に応じて改めて確認・削除する。

## ジョブ運用

### ジョブ状態確認

`/jobs` で全 JobRun を表示:

| 列 | 意味 |
|---|---|
| **idempotency_key** | ジョブ一意キー(例: `render:{video_project_id}:{checksum}`) |
| **status** | `succeeded` / `started` / `failed` |
| **attempt** | 実行試行回数(失敗で +1) |
| **started_at** | 実行開始時刻 |
| **last_error** | 失敗エラーメッセージ |

### 失敗ジョブの確認・復旧

1. `/jobs` で status=`failed` を確認
2. last_error を読む(例: "FFmpeg timed out")
3. **再実行**: "Retry" ボタン → attempt + 1 で新規実行(冪等性で重複なし)

**内部動作**:
- JobRun (status=started) が既に存在 → `JOB_LEASE_TIMEOUT_SECONDS`(3600秒=1時間) 以内なら「実行中」とみなし、再実行しない
- lease 超過 → stale(クラッシュ残骸) → attempt + 1 で再実行

### 状態機械の復旧エッジ

VideoProject は順方向遷移のみ(飛び越し禁止)。**失敗時の巻き戻し**(復旧エッジ: `app/services/state_machine.py` の `_FAILURE_RECOVERY_TARGET`):

```
RESEARCH_FAILED → TOPIC_SCORED
SCRIPT_FAILED → RESEARCH_READY
ASSET_FAILED → SCRIPT_REVIEWED
RENDER_FAILED → ASSETS_READY
REVIEW_FAILED → VIDEO_RENDERED
UPLOAD_FAILED → UPLOAD_READY
METRICS_FAILED → PUBLISHED
```

失敗状態から対応する直前の成功状態に遷移(再実行)。詳細は `app/services/state_machine.py` 参照。

### Celery Worker の監視

#### eager モード(開発)

```powershell
./scripts/dev.ps1 demo
```

全ジョブが同期実行。ログを stdout で確認。

#### compose 環境(本番型)

```powershell
./scripts/dev.ps1 up
```

worker/beat が自動起動:

```powershell
# worker ログ確認
docker logs <container-id>

# worker を再起動
docker restart <worker-container-id>

# beat(定期ジョブ) を再起動
docker restart <beat-container-id>
```

## 予算運用

### 利用状況確認

`/usage` ページ:

```
Daily Budget: $5.00 / $5.00 micro USD (100%)  ← 警告表示
Monthly Budget: $50.00 / $100.00  (50%)

Recent Usage:
- generate_script: 1000 tokens → $0.50 (Fake なら $0)
- synthesize_audio: 200 chars → $0.10
- classify_comments: 500 tokens → $0.05
```

**80% 警告**: 日次/月次で 80% 達すると ⚠️ 警告ログ・管理画面表示。
**100% 停止**: 100% 達するとAI処理停止(generate_script/classify_comments 保留)。
**非AI処理継続**: YouTube アップロード・指標同期・管理画面操作は予算超過でも継続。

### 予算変更

#### 開発時

`.env` を修正:

```env
DAILY_AI_BUDGET_MICRO_USD=5000000      # $5.00
MONTHLY_AI_BUDGET_MICRO_USD=100000000  # $100.00
```

アプリ再起動: `Ctrl+C` → `uv run uvicorn app.main:app --reload`

#### 本番型 (docker compose)

```env
DAILY_AI_BUDGET_MICRO_USD=10000000
```

compose 再起動:

```powershell
./scripts/dev.ps1 down
./scripts/dev.ps1 up
```

### LLM キャッシュによる節約

`app/services/topics/cache.py`:

```python
cache_key = (operation, prompt_version, input_hash)
```

同一企画スコアを2回実行 → 2回目はキャッシュ応答(LLM呼び出しゼロ)。

## バックアップ・リカバリ

### SQLite 環境

**バックアップ**:

```powershell
Copy-Item local.db local.db.backup
```

**復元**:

```powershell
Remove-Item local.db
Copy-Item local.db.backup local.db
./scripts/dev.ps1 migrate  # スキーマ確認(冪等)
```

### PostgreSQL 環境 (docker compose)

**バックアップ**:

```powershell
docker exec <postgres-container> pg_dump -U postgres auto_movie_system > backup.sql
```

**復元**:

```powershell
docker exec -i <postgres-container> psql -U postgres auto_movie_system < backup.sql
```

### 生成物 (generated/)

**構成**: 動画・音声ファイル。git 管理外。

**削除して再生成**:

```powershell
./scripts/dev.ps1 clean-generated
./scripts/dev.ps1 demo  # または個別の Celery タスク
```

**バックアップ**:

```powershell
# 定期的に generated/ を外部ストレージへコピー
Copy-Item -Recurse generated backup_generated_2026-07-05
```

## 障害復旧パターン

### パターン 1: FFmpeg レンダリングタイムアウト

**症状**: `/jobs` で `render:{id}:{checksum}` が `failed` / `MEDIA_FFMPEG_TIMEOUT_SECONDS exceeded`

**対応**:
1. `/jobs` → "Retry" ボタン
2. または `.env` で `MEDIA_FFMPEG_TIMEOUT_SECONDS=600` に増加(デフォルト300秒)
3. アプリ再起動 → Retry

### パターン 2: YouTube アップロード API quota 超過

**症状**: `upload_video` → `QuotaExceededError`

**対応**:
1. YouTube Data API v3 クォータ確認(daily 10,000 units)
2. 24時間待機 → `/jobs` → Retry

fail-closed 設計なので、自動リトライなし。手動で再開。

### パターン 3: LLM 予算 100% 達成

**症状**: `generate_script` → ` BudgetExceededError` / `BudgetLedger reserve failed`

**対応**:
1. `/usage` 画面で確認
2. `.env` で月次予算を増額 → 再起動
3. または月初リセット待機

### パターン 4: DB トランザクション失敗後の状態不整合

**症状**: VideoProject status が進まない / Publication upload_status=STARTED のまま

**対応**:

fail-safe 設計により:
1. JobRun(idempotency_key)一意制約で重複実行防止
2. BudgetLedger の reserve/release が同一トランザクション → 両方消える
3. Publication.idempotency_key 一意制約で二重投稿防止

**冪等性により Retry 安全**: `/jobs` → "Retry" で再実行。
既に成功済みなら JobRun status=`succeeded` なので副作用ゼロ。

### パターン 5: Docker compose の postgres/redis が起動しない

**症状**: `docker logs postgres` で `failed to initialize database`

**対応**:

```powershell
# 全削除・再構築
./scripts/dev.ps1 down
docker volume rm <postgres_data_volume>
./scripts/dev.ps1 up
./scripts/dev.ps1 migrate
```

`docker compose` の `healthcheck` でポーリング確認(最大50秒)。

## モニタリング・ログ

### Eager モード (開発)

```powershell
./scripts/dev.ps1 demo 2>&1 | Tee-Object demo.log
```

スクリーンに出力。ログはマスク済み(SECRET_ENCRYPTION_KEY / tokens 非表示)。

### Compose 環境 (本番型)

**app**:
```powershell
docker logs app
```

**worker**:
```powershell
docker logs worker
```

**beat** (定期ジョブ):
```powershell
docker logs beat
```

**全サービス**:
```powershell
docker compose logs -f
```

### Structured Logging

`app/core/logging.py` で structlog 有効化:

```python
import structlog
logger = structlog.get_logger(__name__)
logger.info("task_started", idempotency_key="...", operation="...")
```

出力: JSON形式・タイムスタンプ・レベル・マスキング済み。

## 定期メンテナンス

### 日次

- `/usage` で予算確認(80% 以上なら節制)
- `/jobs` で失敗確認・Retry
- beat/workerログで前日分セルフレビューの候補数・失敗数を確認

### 週次

- `/metrics` でトレンド確認
- `/comments` で異常コメント監視

### 月次

- DB バックアップ
- `generated/` 容量確認
- ジョブ履歴クリーンアップ(古い JobRun 削除、要カスタム実装)

## 次ステップ

- **docs/security.md** でセキュリティ対策確認
- **docs/cost-control.md** で料金表・操作上限理解
- **DECISIONS.md** で設計背景(ADR-0005 reconcile等)確認
