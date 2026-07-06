# TASKS

進行状況の正本。各Phase終了時に更新する。

## 状態

- [x] Phase -1: 環境セットアップ(Python 3.12.13 via uv / FFmpeg 8.1.2 via winget / Docker Desktop インストール済・初回起動未)
- [x] Phase 0: 足場 + Claude Code設定 + architectレビュー(ADR-0004〜0007承認、D-009〜D-012)
- [x] Phase 1: 基盤(FastAPI/DB/Alembic/Celery/Docker/CI)— unit 12件パス、ruff/mypyクリーン。Docker実起動とCI上のPG統合テストは未検証(未解決事項参照)
- [x] Phase 2: 企画・台本・コスト管理 — 2A: モデル群+スコアリング+インポート+冪等性基盤 / 2B: LLM抽象化(Fake/Anthropic)+LLMキャッシュ+予算reserve→commit/release+台本生成+検査。unit+contract 73件パス
- [x] Phase 3: 動画生成 — 状態機械(ADR-0006全エッジ)/VideoProject/Asset/TTS(Fake+GenericCommand)/字幕SRT+VTT/FFmpegレンダリング/ffprobe検査/冪等パイプライン。実ffmpegで1920x1080 H.264+AAC 7.2秒MP4生成・再実行スキップ確認。unit+contract 139件+media 2件パス
- [x] Phase 4: 自動レビュー + 承認 — 機械検査(silencedetect/volumedetect含む)/コンテンツ検査(ルール+LLM)/公開可否ゲート(fail-closed)/承認・却下(CSRF+監査ログ)。unit 179件+media 5件パス
- [x] Phase 5: YouTube投稿 — Publication/OAuthToken モデル+マイグレーション、Fernet暗号化(app/core/crypto.py)、
      YouTubeProvider Protocol(Fake/Real)、resumable upload(google-api-python-client)、
      アップロード2段階記録+reconcile(ADR-0005: description内 `amx-idem:{key}` マーカー突合)、
      公開予約サービス(6条件ゲート: can_auto_publish 4条件+重複youtube_video_idなし+有効なOAuth認証)、
      Celeryタスク+API(POST /api/video-projects/{id}/upload, /api/publications/{id}/schedule)、
      OAuthセットアップスクリプト+手順書(docs/youtube-oauth.md)。unit/contract 46件追加、計225件パス。
      alembic upgrade/downgrade往復・ruff・mypy クリーン
- [x] Phase 6: 分析・コメント・フィードバック — VideoMetricDaily/Comment/Insight モデル+マイグレーション、
      Fake/Real YouTubeProvider の統計・コメント取得口を利用した指標同期/コメント差分同期、
      削除済みコメントの状態更新、ルールベース分類(QUESTION/NEXT_TOPIC_REQUEST等)、
      コメント/指標由来Insight生成、コメント由来Topic候補生成、
      API(同期・一覧取得) + Celeryタスク(単体/一括同期)。unit/API 9件追加、計240件パス。
      alembic upgrade head・ruff・mypy クリーン
- [x] Phase 7: 管理画面 + 運用ドキュメント — 7A: 管理画面 12ページ構築(dashboard/topics/video-projects/reviews/approvals/publications/comments/insights/jobs/usage/settings/health)、CSRF保護・監査ログ。7B: demo・E2Eテスト・全フロー完走検証。ドキュメント整備: README.md / docs/setup.md / docs/operations.md / docs/security.md / docs/content-policy.md / docs/cost-control.md/.env.example / TASKS.md更新。unit/API 240件+e2e全フロー検証パス。
      7C: 外部レビュー指摘6件対応(D-019) — ①`app/core/auth.py`(HTTP Basic認証、
      ADMIN_PASSWORD未設定時はAPP_ENV=development/testのみバイパス・それ以外401)を
      web/api全ルーターへ適用(/healthのみ除外)。②承認/却下の`decided_by`を
      フォーム入力から認証ユーザーへ変更(review_detail.htmlから入力欄削除、
      操作者を画面表示)。③`/publications`に公開予約フォーム実装
      (POST /publications/{id}/schedule、ゲート拒否時はフラッシュ表示・500にしない)。
      ④`scripts/demo.py`はDEMO_USE_CURRENT_DB=1明示時のみ既存DATABASE_URLを尊重し、
      それ以外は常にdemo.dbへ強制上書き(接続先DBをマスク表示)。⑤`finalize_due_publications`
      (published_at確定+PUBLISHED→METRICS_COLLECTING遷移、二重更新なし)をscheduler.pyへ追加し
      Celery beatへ15分毎タスク登録。⑥pip-audit導入(security-check組み込み、CI security
      ジョブ追加、脆弱性0件確認)。unit 24件追加、計264件パス(既存253件超過)。ruff/mypyクリーン。
- [ ] Phase 8: 総合検証

## 未解決事項

- Docker Desktop 初回起動中(GUI初期化待ち。docker CLI が PATH 未反映。compose 検証は Phase 8)
- LLM料金表(MODEL_PRICING)と operation別上限(OPERATION_LIMITS)はコード内定数。料金改定時はコード変更が必要(MVP許容)
- スコア再計算は idempotency_key 固定のため初回のみ。再スコアリング運用は将来対応
- Review.score 採点式(blocking-25/warning-5)は暫定。運用要件確定後に見直し
- CSRF鍵は SECRET_ENCRYPTION_KEY 未設定時に開発用フォールバック。本番はfail-fast必須化を Phase 8 セキュリティレビューで確認
- ffmpeg/docker は PATH 未反映。設定のパス解決(D-007)で吸収する
- ADR-0005 の reconcile 実装は Phase 5 で完了(FakeYouTubeProvider + RealYouTubeProvider 共通契約。
  contract テストで型・戻り値を検証。実アカウントでの reconcile 実地検証はMVP外)
- 公開ゲートの「重複youtube_video_idなし」「有効なOAuth認証」の2条件は
  `app/services/reviews/gate.py`(既存4条件, 同期API)を変更せず、
  `app/services/publishing/scheduler.py` 側で追加検証する設計とした(gate.py の既存契約・
  テストを壊さないため)。将来 gate.py を6条件対応の非同期APIへ統合するかは要検討
- RealYouTubeProvider は呼び出しごとに DB から最新の OAuthToken を読み込みリフレッシュする
  (キャッシュしない)。トークンローテーション頻度が高い場合は性能要件を見て見直す
- HTTP Basic認証(D-019)はMVP相当の最小実装。ユーザー管理・ロールベース権限・
  レート制限/ブルートフォース対策は未実装(本番運用前にリバースプロキシ側TLS必須+
  必要なら多要素化を検討)。ADMIN_PASSWORD は平文設定のため secrets manager 等への
  移行は将来対応

## 完了条件

仕様§23の20項目。詳細は docs/architecture.md 参照。
