# TASKS

進行状況の正本。各Phase終了時に更新する。

## 状態

- [x] Phase -1: 環境セットアップ(Python 3.12.13 via uv / FFmpeg 8.1.2 via winget / Docker Desktop インストール済・初回起動未)
- [x] Phase 0: 足場 + Claude Code設定 + architectレビュー(ADR-0004〜0007承認、D-009〜D-012)
- [x] Phase 1: 基盤(FastAPI/DB/Alembic/Celery/Docker/CI)— unit 12件パス、ruff/mypyクリーン。Docker実起動とCI上のPG統合テストは未検証(未解決事項参照)
- [x] Phase 2: 企画・台本・コスト管理 — 2A: モデル群+スコアリング+インポート+冪等性基盤 / 2B: LLM抽象化(Fake/Anthropic)+LLMキャッシュ+予算reserve→commit/release+台本生成+検査。unit+contract 73件パス
- [x] Phase 3: 動画生成 — 状態機械(ADR-0006全エッジ)/VideoProject/Asset/TTS(Fake+GenericCommand)/字幕SRT+VTT/FFmpegレンダリング/ffprobe検査/冪等パイプライン。実ffmpegで1920x1080 H.264+AAC 7.2秒MP4生成・再実行スキップ確認。unit+contract 139件+media 2件パス
- [x] Phase 4: 自動レビュー + 承認 — 機械検査(silencedetect/volumedetect含む)/コンテンツ検査(ルール+LLM)/公開可否ゲート(fail-closed)/承認・却下(CSRF+監査ログ)。unit 179件+media 5件パス
- [ ] Phase 5: YouTube投稿(進行中)
- [ ] Phase 5: YouTube投稿
- [ ] Phase 6: 分析・コメント・フィードバック
- [ ] Phase 7: 管理画面 + 運用
- [ ] Phase 8: 総合検証

## 未解決事項

- Docker Desktop 初回起動中(GUI初期化待ち。docker CLI が PATH 未反映。compose 検証は Phase 8)
- LLM料金表(MODEL_PRICING)と operation別上限(OPERATION_LIMITS)はコード内定数。料金改定時はコード変更が必要(MVP許容)
- スコア再計算は idempotency_key 固定のため初回のみ。再スコアリング運用は将来対応
- Review.score 採点式(blocking-25/warning-5)は暫定。運用要件確定後に見直し
- CSRF鍵は SECRET_ENCRYPTION_KEY 未設定時に開発用フォールバック。本番はfail-fast必須化を Phase 8 セキュリティレビューで確認
- ffmpeg/docker は PATH 未反映。設定のパス解決(D-007)で吸収する
- ADR-0005 の reconcile 実装は Phase 5(Fake で E2E 検証)

## 完了条件

仕様§23の20項目。詳細は docs/architecture.md 参照。
