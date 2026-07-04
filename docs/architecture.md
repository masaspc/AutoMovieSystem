# アーキテクチャ

## 全体構成

モジュラーモノリス。1つのFastAPIアプリ + Celeryワーカー + PostgreSQL + Redis。
外部サービス(LLM/TTS/YouTube/ストレージ)はすべて `app/providers/` の Protocol で抽象化し、
Fake実装で全ワークフローが完結する。n8n等の外部オーケストレーターには依存しない
(将来のn8n連携はWebhook API経由の任意機能)。

```
┌─────────────────────────────────────────────────────┐
│ FastAPI (app)                                        │
│  ├ app/web    Jinja2+HTMX 管理画面 (/dashboard 等)   │
│  ├ app/api    JSON API (+ 将来のWebhook受口)          │
│  └ /health                                           │
├─────────────────────────────────────────────────────┤
│ app/services  ドメインロジック(状態機械・ゲート)     │
│ app/repositories  DBアクセス                          │
│ app/providers  llm / tts / youtube / storage (Protocol)│
├──────────────┬──────────────┬───────────────────────┤
│ PostgreSQL   │ Redis        │ Celery worker + beat   │
└──────────────┴──────────────┴───────────────────────┘
```

## データフロー

```
[企画ソース: 手動/CSV/コメント派生]
   → Topic 作成 → score_topic → RESEARCH_READY(Evidence登録)
   → generate_script (LLM) → review_script → SCRIPT_REVIEWED
   → prepare_assets → synthesize_audio (TTS) → render_video (FFmpeg)
   → inspect_video (ffprobe/機械検査) → review_content (LLM)
   → AUTOMATED_REVIEW_PASSED → [人間承認: 管理画面] → HUMAN_APPROVED
   → upload_video (YouTube, private) → schedule/publish(条件付き)
   → sync_video_metrics / sync_comments (定期)
   → classify_comments → generate_insights → 次回 Topic 候補
```

## 状態遷移(VideoProject.status)

正常系(順方向のみ、飛び越え禁止):

```
TOPIC_CREATED → TOPIC_SCORED → RESEARCH_READY → SCRIPT_GENERATED
→ SCRIPT_REVIEWED → ASSETS_READY → VIDEO_RENDERED → AUTOMATED_REVIEW_PASSED
→ HUMAN_APPROVED → UPLOAD_READY → UPLOADED_PRIVATE → SCHEDULED
→ PUBLISHED → METRICS_COLLECTING → FEEDBACK_GENERATED
```

失敗状態: RESEARCH_FAILED / SCRIPT_FAILED / ASSET_FAILED / RENDER_FAILED /
REVIEW_FAILED / UPLOAD_FAILED / METRICS_FAILED

追加状態・エッジ(ADR-0006):
- REJECTED: AUTOMATED_REVIEW_PASSED からの人間却下先(終端)
- 各失敗状態は対応する処理の直前状態からのみ遷移可。復旧エッジは
  「失敗状態 → その処理の入力となった直前の正常状態」を遷移表に明示する(再実行=巻き戻し)
- UPLOADED_PRIVATE は正当な準終端(AUTO_PUBLISH_ENABLED=false かつ予約なしなら PUBLISHED へ進まない)
- metrics/comments の定期同期は status ではなく「published_at 非NULL の Publication」述語で駆動する

実装: `app/services/state_machine.py` に遷移表(dict)を持ち、`transition(project, to_state)` のみが
status を変更できる。遷移表外は `InvalidTransitionError`。

## 冪等性設計

- すべてのジョブは決定的な `idempotency_key`(例: `render:{video_project_id}:{script_checksum}`)を持つ
- `JobRun(idempotency_key)` 一意制約。実行開始時に JobRun を INSERT し、既に成功済みなら即スキップ
- 成果物(音声・動画)は生成前に既存ファイル+チェックサム照合、あれば再生成しない
- アップロードは2段階: (1) `Publication` を `upload_status=STARTED` でINSERT(`idempotency_key`一意制約)
  → (2) 実行 → (3) `youtube_video_id` 記録+`COMPLETED`。アップロード時に動画説明末尾へ
  idempotency マーカーを埋め込み、STARTED かつ youtube_video_id 不明の再試行では自チャンネルの
  最近のアップロード一覧とマーカーを突合(reconcile)してから再開する。突合が不確実な場合は
  再アップロードせず UPLOAD_FAILED として保留する(fail-closed)。詳細は ADR-0005
- UsageRecord はジョブの JobRun 成功と同一トランザクションで記録し、二重計上を防ぐ

## 公開ゲート(fail-closed)

自動公開はすべての条件を満たす場合のみ。1つでも欠ければ private のまま保留:

1. 自動レビュー合格(blocking findings = 0)
2. Approval レコードあり(REQUIRE_HUMAN_APPROVAL=true の場合必須)
3. アップロード対象ファイルのチェックサム一致
4. 公開メタデータ確定
5. 重複 youtube_video_id なし
6. 有効な OAuth 認証
かつ `AUTO_PUBLISH_ENABLED=true`(デフォルト false)

## コスト制御

- 全 LLM/TTS 呼び出し → UsageRecord(provider/model/operation/tokens/estimated_cost)
- 日次・月次予算(設定値)。80%で警告ログ+管理画面表示、100%でAI処理を保留キューへ
- 非AI処理(統計収集・投稿・管理画面)は予算超過でも継続
- LLMキャッシュ: (operation, prompt_version, input_hash) をキーに応答を保存

## モデルルーティングポリシー(LLM)

| operation | ポリシー |
|---|---|
| コメント分類・文章整形・類似判定 | low(低コスト) |
| 企画候補生成 | low〜mid |
| 台本初稿 | mid |
| 技術整合性レビュー・公開前最終判定 | high(または人間) |

上限(トークン・費用)超過時は高価なモデルへ自動昇格せず、保留してレビュー対象にする。
構造化出力の修復リトライは最大1回。

## ドメインモデル

仕様§6の14エンティティ: Channel, Topic, Evidence, Script, VideoProject, Asset, Review,
Approval, Publication, VideoMetricDaily, Comment, Insight, JobRun, UsageRecord。

主要制約:
- `JobRun.idempotency_key` UNIQUE / `Publication.idempotency_key` UNIQUE
- `Publication.youtube_video_id` UNIQUE(NULL許容)
- `Comment.youtube_comment_id` UNIQUE / `VideoMetricDaily(publication_id, metric_date)` UNIQUE
- `Script(topic_id, version)` UNIQUE / `Review(video_project_id, reviewer_type, review_version)` UNIQUE
- `Topic(channel_id, source_type, source_ref)` UNIQUE(source_ref は取り込み元の自然キー。
  手動入力はクライアント生成キー、CSVは行ハッシュ、コメント派生は youtube_comment_id)— ADR-0004
- `VideoProject(topic_id, generation)` UNIQUE(MVPは generation=1 固定運用)— ADR-0004
- `UsageRecord(job_run_id, seq)` UNIQUE(job_run_id 非NULL時)— ADR-0004
- 金額はすべて 整数マイクロUSD(`*_micro_usd`)で保存。float禁止 — ADR-0007
- 予算は BudgetLedger 行への条件付きUPDATE(reserve→commit/release)でアトミックに消費 — ADR-0007
- Topic.total_score は設定可能な重み(§8)で services/topics/scoring.py が計算

## テスト戦略

- unit: SQLite in-memory + eager Celery + Fake providers
- integration: PostgreSQL + Redis(docker compose / CIサービスコンテナ)。`-m integration`
- contract: Fake と実装クラスが同じ Protocol 契約を満たすことを共通テストベースで検証
- e2e: 企画→Insight のドライラン全工程を2回実行し、重複ゼロを検証

## セキュリティ要点

- OAuthリフレッシュトークンは Fernet(`SECRET_ENCRYPTION_KEY`)で暗号化してDB保存
- structlog プロセッサでシークレットパターンをマスキング
- subprocess は引数配列固定・shell禁止(`app/core/subprocess_util.py` 経由のみ)
- ファイルパスは `generated/` / `assets/` 境界検証(`app/core/paths.py`)
- 外部URL取得(RSS等)は タイムアウト・サイズ上限・リダイレクト上限・プライベートIP拒否
- 管理画面の変更系操作は CSRF トークン必須。承認等の重要操作は監査ログ(decided_by 記録)
