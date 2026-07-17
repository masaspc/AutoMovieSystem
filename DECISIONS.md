# DECISIONS

採用した前提・決定事項の正本。番号付きで追記のみ(覆す場合は新番号で記録)。

## D-001: リポジトリルート直下に構築(2026-07-04)

仕様の `youtube-growth-automation/` サブディレクトリは作らず、git リポジトリルート
(`AutoMovieSystem/`)直下に構築する。ネストは import パスと CI 設定を複雑にするだけで利点がない。

## D-002: uv による Python 3.12 管理(2026-07-04)

システム Python が存在しない(MS Store スタブのみ)ため、uv で Python 3.12.13 を導入。
依存管理も uv(pyproject.toml + uv.lock)。実行は `uv run <cmd>`。

## D-003: SQLite 互換を維持した PostgreSQL 設計(2026-07-04)

本番・docker compose は PostgreSQL 16。単体/E2E テストの高速化と Docker 非依存の
ローカル検証のため、モデルは SQLite でも動く型のみ使用(JSONB でなく JSON、
ARRAY でなく JSON、サーバーサイド UUID でなくアプリ生成の文字列 UUID)。
PostgreSQL/Redis 前提の統合テストは `integration` マーカーで分離し、CI のサービス
コンテナと docker compose 環境で実行する。

## D-004: Celery eager モード切替(2026-07-04)

`CELERY_TASK_ALWAYS_EAGER=true` で Redis なしにジョブを同期実行できるようにする。
`make demo` と E2E テストはデフォルトで eager。compose 環境では通常の worker/beat。

## D-005: Windows 開発コマンドの二重化(2026-07-04)

Makefile(CI・Linux/Mac・Git Bash 用)と `scripts/dev.ps1`(Windows PowerShell 用)を
同一ターゲット名で併設。ローカル検証は dev.ps1 を正とする(make が未導入のため)。

## D-006: 実 API はキー未設定でも全機能デモ可能(2026-07-04)

Anthropic/YouTube/TTS の実クライアントは実装するが、API キー・OAuth 未設定の環境では
Fake プロバイダーで全ワークフローが動作する。プロバイダー選択は設定
(`LLM_PROVIDER=fake|anthropic` 等)。テストは常に Fake。

## D-007: FFmpeg は winget 導入 + フルパス解決(2026-07-04)

winget(Gyan.FFmpeg)で導入。PATH 反映遅延に備え、設定の `FFMPEG_PATH`/`FFPROBE_PATH` が
未指定なら PATH → WindowsApps エイリアス → winget Links の順で解決するロジックを持つ。

## D-008: 動画生成物は `generated/` 配下のみ(2026-07-04)

パス検証はこのディレクトリ配下であることを必須にし、ディレクトリトラバーサルを拒否。
git 管理外。`make clean-generated` で削除可能。

## D-009: ADR-0004〜0007 を承認(2026-07-04, architectレビュー起点)

architect(opus)の設計レビュー指摘(BLOCKER 2件)を受け、ADR-0004(エンティティ生成の
冪等キー)、0005(アップロードreconcile・fail-closed)、0006(状態機械の復旧エッジ/
REJECTED/準終端)、0007(整数マイクロUSD+予算アトミック予約)をすべて承認。

## D-010: VideoProject は (topic_id, generation) 世代管理(2026-07-04)

1 Topic = 1 Project 固定にせず、`(topic_id, generation)` UNIQUE を最初から導入。
MVPでは generation=1 のみ使用。作り直し要件が出ても スキーマ変更不要。

## D-011: 予算予約はMVPスコープに含める(2026-07-04)

check-then-act 競合は eager 単一ワーカーでは顕在化しないが、BudgetLedger への
条件付きUPDATE(SQLite/PG両対応)で実装コストが低いため、MVPで reserve→commit/release を実装。

## D-012: reconcile はFakeで完全実装、実YouTubeは同一コードパス(2026-07-04)

ADR-0005 の reconcile(idempotencyマーカー突合)は Fake YouTube に list API を持たせて
E2Eで検証する。実プロバイダーも同じインターフェースを実装(実アカウント検証はMVP外)。

## D-013: DB は同期 SQLAlchemy、プロバイダーは async(2026-07-04)

Celery タスク(同期)と FastAPI の両方から同じ repository/service を使うため、DB アクセスは
同期 SQLAlchemy 2.0(psycopg 3 / sqlite3)。LLM/TTS/YouTube プロバイダーは仕様§9どおり
async Protocol とし、Celery タスク内では asyncio.run() で呼ぶ。FastAPI の DB 依存
エンドポイントは def(スレッドプール実行)にする。

## D-014: アップロード冪等性は Publication(idempotency_key)+JobRun の二重管理(2026-07-05)

`app/services/publishing/uploader.py` の `upload_video` は、他サービス同様
`run_idempotent_async`(JobRun)でラップしつつ、内部で `Publication.idempotency_key`
(`upload:{video_project_id}:{checksum}`)の get-or-create を行う。JobRun が既に
succeeded ならバリデーション自体を再実行せずスキップする(2回目呼び出しで
VideoProject の状態が UPLOADED_PRIVATE に進んでいても前提検証エラーにならないため)。
ADR-0005 の reconcile(description内 `amx-idem:{idempotency_key}` マーカー突合)は
`youtube_video_id` が未記録の場合に常に実行する(started/failed問わず、実行直前に
必ず1回 list する。ADR-0005 Consequences の想定どおり quota を1回消費する)。

## D-015: 公開ゲート6条件は gate.py(4条件)+ scheduler.py(2条件)に分割(2026-07-05)

`docs/architecture.md` の公開ゲート6条件のうち、既存 `app/services/reviews/gate.py`
の `can_auto_publish`(同期API、Phase 4で実装済み・テスト済み)は
自動レビュー合格/人間承認/チェックサム一致/高リスクキーワードの4条件を担当する。
残る「重複 youtube_video_id なし」「有効なOAuth認証」の2条件は、YouTubeProvider
(async)と Publication モデル(Phase 5で新設)に依存するため、gate.py の既存契約・
シグネチャ(同期・provider引数なし)を変更せず `app/services/publishing/scheduler.py`
の `_check_full_publish_gate` で追加検証する。gate.py を6条件対応の非同期APIへ
統合するかは将来の要検討事項(TASKS.md 未解決事項参照)。

## D-016: JobRun は lease 方式で並行実行を防止(2026-07-05, 外部レビュー起点)

status="started" の JobRun は `started_at + JOB_LEASE_TIMEOUT_SECONDS`(デフォルト3600秒)
以内なら「実行中」とみなし fn を実行せず in_progress を返す(サービス層は
JobInProgressError を送出、API は 409 にマップ)。lease 超過はクラッシュ残骸として
attempt+1 で再実行。二重投稿・二重レンダリング防止。

## D-017: 失敗記録は新規セッションで永続化(2026-07-05, 外部レビュー起点)

Celeryタスクの except では session.rollback() 後に、新規セッションで
JobRun(failed+last_error)・失敗状態遷移(RENDER_FAILED/UPLOAD_FAILED等)・
Publication(upload_status="failed")を再記録する。例外には idempotency_key /
publication_idempotency_key 属性を付与してタスクラッパーへ伝搬する。
予算 reserve/release は同一トランザクション内で両方消えるため残高整合は保たれる。

## D-018: Asset.role 列で成果物の同一性をDB制約化(2026-07-05, 外部レビュー起点)

Asset に role 列("background" / "audio:{n}" / "subtitle:srt|vtt" / "endcard")+
UNIQUE(video_project_id, role)。マイグレーションの既存行 backfill は meta JSON から
意味的に導出(導出不能・衝突時のみ id フォールバック)。

## D-019: 管理画面/API全体にHTTP Basic認証をfail-closedで導入(2026-07-05, 外部レビュー起点)

`app/core/auth.py` の `require_admin` dependency を web ルーター群・api ルーター群
全体(`app.include_router(..., dependencies=[Depends(require_admin)])`)に適用する。
`/health` のみ監視用途のため認証対象から除外する。

判定ロジック(fail-closed):
- `ADMIN_PASSWORD` が設定済み: `secrets.compare_digest` でユーザー名・パスワード双方を
  タイミング攻撃耐性のある方法で検証する。不一致は 401(`WWW-Authenticate: Basic`)。
- `ADMIN_PASSWORD` 未設定: `APP_ENV` が `development`/`test` の場合のみ認証をスキップし
  (ユーザー名 `dev-anonymous` を返す。プロセス起動後初回に警告ログを1回出力)、
  それ以外の `APP_ENV`(例: `production`)では常に401とする(パスワード未設定の
  本番デプロイを事故で許してしまわないため)。

承認/却下フロー(`app/web/approvals.py`)は認証ユーザー名を `decided_by` としてそのまま
使う(フォームでの自己申告を廃止)。テスト(`tests/conftest.py`)は `APP_ENV=test` を
明示することで既存挙動(認証バイパス)を維持する。

代替案として「専用の認証テーブル+セッションCookie」も検討したが、MVPの管理者1〜数名
運用にはHTTP Basic + fail-closedデフォルトで十分と判断した(ユーザー管理・ロール分離は
将来要件、TASKS.md未解決事項参照)。

## D-020: OpenAI互換ローカルLLM + model_policy別プロバイダールーティング(2026-07-06)

Ollama / LM Studio / vLLM が共通で話せる OpenAI互換 Chat Completions API
(`POST {base_url}/chat/completions`)を話す `LocalLLMProvider`
(`app/providers/llm/local_openai.py`)を追加した。既存の `AnthropicLLMProvider` と同じ
`LLMProvider` Protocol・リトライ方針(tenacity、接続エラー/5xxを指数バックオフ最大3回)を
踏襲するが、以下の点が異なる:

- 構造化出力は `response_format={"type": "json_object"}` + システムプロンプトへの
  JSON Schema(`response_schema.model_json_schema()`)埋め込みで指示する
  (Anthropicの `tool_use` 強制と異なり、ローカルモデルはtool useを持たない前提)。
  スキーマ不適合時の修復は自身で行わず、既存 `app/services/llm_gateway.py` の
  修復リトライ(最大1回)に委ねる(anthropic.pyと同じ「provider自身は検証しない」契約)。
- `estimated_cost_micro_usd` は常に `0`(ローカル実行はAPI課金が発生しない)。
  `UsageRecord` には `provider="local"`、`model=` 実モデル名(例: `qwen3:32b`)で記録される。
- usageフィールド(prompt_tokens/completion_tokens)が無い応答向けに、文字数からの
  トークン数概算フォールバックを持つ(`fake.py` と同じ流儀)。

また `LLM_PROVIDER_LOW`/`LLM_PROVIDER_MID`/`LLM_PROVIDER_HIGH`(空文字なら `LLM_PROVIDER`
に従う)で `model_policy` ごとに異なるプロバイダーを選択できるようにした
(`app/providers/llm/routing.py` の `RoutingLLMProvider`、`app/providers/llm/factory.py` の
`get_llm_provider` が全ポリシー同一名なら従来どおり単一プロバイダーを返し、異なる場合のみ
ラップする)。ハイブリッド構成(LOW/MID=local、HIGH=anthropic)を想定し、公開可否に関わる
`high` ポリシー(仕様§9)はローカル小型モデルより高性能モデルまたは人間レビューを推奨する
旨を `docs/local-llm.md` に明記した(`REQUIRE_HUMAN_APPROVAL=true` のfail-closedデフォルトが
最終防御になる)。

## D-021: グロース機能は「模倣=フォーマット研究」「量産=自動レビューまで」(2026-07-06)

登録者0→1000目標のサイト構成として /growth(成長ダッシュボード)・/benchmarks
(ベンチマーク)・量産バッチを追加した。設計上の一線:

- ベンチマークは他チャンネル動画の「構成・フォーマットの研究」であり、コンテンツの
  転載・流用は行わない(派生企画のdescriptionにもその旨を自動記載)
- 量産バッチ(run_production_batch)は自動レビュー通過までしか進めない。承認・公開は
  人間の操作を必須のまま残す(fail-closed維持)。制作工程は run_production_pipeline
  として orchestration から抽出し、フルパイプラインと共通化
- 人為的な再生・登録・評価・コメントを発生させる機能は実装しない(CLAUDE.md絶対原則)
- 勝ちパターン特定は「登録効率(1000再生あたり登録増)」を第一ソートキーとする

## D-022: 希望尺はProductionSettings、レビュー期待尺はTTS実測値(2026-07-12)

動画の制作方針は`ProductionSettings`として`VideoProject.production_settings`のJSON列へ保存する。
プリセット、希望尺、許容範囲、セクション数、構成テンプレート、トーン、掛け合い比率を含み、
正規化JSONのchecksumを台本生成の冪等キーへ含める。設定変更時は新しいScript versionを生成する。

`VideoProject.target_duration_seconds`は従来どおり機械レビュー用の期待尺とし、希望尺の保存には
使用しない。TTS合成のたびに音声Assetの実測合計+エンドカード尺で上書きし、古い実測値を残さない。
LLM生成直後の推定は音声化対象のsectionだけを日本語300文字/分で計算し、範囲外なら最大2回修復する。
修復も通常のLLM gatewayを通すためUsageRecord・予算予約・キャッシュの対象になる。Evidence IDが
増減する修復は根拠の欠落・捏造を避けるため破棄し、上限後も範囲外なら警告を残してベストエフォートで保存する。

## D-023: シリーズは全台本ではなく全Episode Planを先に固定する(2026-07-12)

講座シリーズでは、最初に全話の完成台本を生成せず、SeriesPlanと全Episode Planを生成・編集・
人間承認する。その後、各話の詳細台本を順番に生成する。これにより途中修正の再生成コストを抑え、
過去回の説明済み概念、今回の新規概念、未説明概念を各台本の制約として利用できる。

Episodeから作るTopicは`series:{series_id}:episode:{episode_id}`を自然キーにし、再操作でも重複しない。
制作開始済みEpisodeが1件でもあれば全体再生成を拒否し、既存動画との学習順序をfail-closedで保護する。
Episode編集・並べ替え後はシリーズをdraftへ戻し、再承認を要求する。

## D-024: 学習動画は「装飾の多さ」ではなく意味のある画面遷移を正とする(2026-07-12)

各ScriptSectionのJSONに`visual_type`、教材表示内容、背景スタイル、キャラクター配置を保存する。
DB列は追加せず既存Script.bodyの後方互換を維持する。code/key_point/quiz/diagram/steps/dialogueを
内容に応じて使い分け、単なるランダム背景や常時アニメーションは採用しない。

字幕は画面下部の専用セーフエリアへ半透明背景付きで焼き込み、キャラ名は上部へ分離する。
キャラクターは発話強調・呼吸・感情リアクションに限定して動かし、コードやクイズでは縮小または
非表示にして教材を主役にする。レンダリング時には秒単位のscene_manifestを保存し、YouTube
Analyticsの維持率低下点をvisual_typeへ対応付けてInsightを生成する。

## D-025: 人間却下は旧世代の終端、新世代を台本から再開できる(2026-07-12)

REJECTEDから同一VideoProjectを巻き戻すと、却下時に確認した動画・Review・Approvalとの対応が
曖昧になるため行わない。却下済みProjectは終端のまま保持し、同一Topicでgenerationを1増やした
VideoProjectを新規作成してRESEARCH_READYまで正規遷移させる。新Project IDを台本生成の
regeneration_keyへ含め、同一制作設定でも過去のLLMキャッシュを再利用せず新しいScript versionを
生成する。素材・字幕・動画は新世代で改めて作成し、旧世代の監査証跡は変更しない。

## D-026: 投稿後セルフレビューとトレンド即応は公開ゲートを変えずに自動化する(2026-07-13)

トレンド収集はRSS 2.0/Atomを対象とし、扱う情報を見出し、検証済みのhttp(s)リンク、
200字以内の短い要約に限定する。記事本文は取得・保存・転載しない。Providerの既定値は
`fake`とし、テスト・デモ環境から意図せず外部ネットワークへ接続しない。

トレンド記事の即動画化では、URLハッシュで重複を防ぎ、`preset="short"`、
`script_template="news_commentary"`、`bgm_mood="serious"`の制作設定で既存パイプラインを
自動レビュー完了まで実行する。人間による承認・投稿操作は従来どおり必須とし、公開の
fail-closedゲートは緩和しない。

投稿後セルフレビューはPublicationと指標日を冪等キーにし、同じ日の重複LLM実行を防ぐ一方、
新しい日の指標では再レビューできる。投稿一覧・動画詳細からの手動実行に加え、Celery beatで
UTC前日分の指標がある投稿を日次処理する。生成した`self_review` Insightは次回台本のプロンプトへ
自動注入し、改善提案画面で該当Insightを削除すれば以後の注入対象から外れるものとする。

## D-027: 成長自動化は「例外だけ人間確認」、YouTube書き込みは明示操作とする(2026-07-14)

YouTube公式の推薦説明はAppeal(選ばれるか)、Engagement(見続けるか)、Satisfaction
(満足したか)を一体として扱い、CTRだけを最大化する方針ではない。また、収益化ポリシーは
反復的・大量生産的なinauthentic contentをチャンネル全体で評価する。そのため本システムの
自動化は投稿本数ではなく、品質ゲートを通過した候補数を最大化する。

レンダリング前に成長品質プリフライトを実行し、タイトル/サムネイルの約束一致、冒頭の価値提示、
維持構成、登録理由、独自性、根拠・信頼性を構造化確認する。基準未達はEvidence IDを保持したまま
最大1回だけ自動修正し、それでも未達、最近の動画との高類似、根拠未確認、高リスク領域は詳細確認へ
送る。合格候補はタイトル・サムネイル採用案まで自動選択する。

日次オートパイロットは既定OFFとし、環境設定で対象チャンネルと1日上限を明示した場合だけ、
トレンド収集から自動レビューまでを実行する。人間の最終確認画面には対象チャンネル、動画、
タイトル、サムネイル、説明、出典、レビュー、AI開示、公開範囲を集約する。ユーザーの1回の明示操作で
承認とYouTubeへの**非公開**アップロードを開始できるが、公開・公開予約は従来どおり別の明示操作とし、
完全無人公開は行わない。

YouTube AnalyticsのCTR・維持率・登録増等は公式の生指標として表示し、本システム独自の台本品質評価と
混同しない。過去実績を生成プロンプトへ渡す場合も「過去の観測」であり、因果関係や成功保証ではないと
明示する。

## D-028: 台本・映像の演出差分はID起点のsha256決定論抽選とする(2026-07-18)

動画の単調さを抑えるため、導入フック、挿入コーナー、ツッコミ密度、セクション間ブリッジ、
Ken Burns方向、場面転換、見出し形式、アクセント配色、エンディングを動画ごとに変化させる。
ただし再生成の冪等性と監査可能性を保つため、非シード乱数は使わず、台本はtopic_id、映像は
video_project_idを起点としたsha256で決定論的に選ぶ。抽選結果はScript.source_manifestへ保存し、
将来の維持率との相関分析に利用できる形にする。

構成テンプレートに不適合な演出は抽選候補から除外し、news_commentaryにはクイズを入れず、shortsの
挿入コーナーは最大1件とする。既存成果物の誤再利用を避けるため、台本プロンプト、背景素材、
レンダリングの各仕様バージョンを演出ロジック変更時に更新する。
