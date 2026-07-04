# TASKS

進行状況の正本。各Phase終了時に更新する。

## 状態

- [x] Phase -1: 環境セットアップ(Python 3.12.13 via uv / FFmpeg 8.1.2 via winget / Docker Desktop インストール済・初回起動未)
- [x] Phase 0: 足場 + Claude Code設定 + architectレビュー(ADR-0004〜0007承認、D-009〜D-012)
- [ ] Phase 1: 基盤(FastAPI/DB/Alembic/Celery/Docker/CI)(進行中)
- [ ] Phase 2: 企画・台本・コスト管理
- [ ] Phase 3: 動画生成
- [ ] Phase 4: 自動レビュー + 承認
- [ ] Phase 5: YouTube投稿
- [ ] Phase 6: 分析・コメント・フィードバック
- [ ] Phase 7: 管理画面 + 運用
- [ ] Phase 8: 総合検証

## 未解決事項

- Docker Desktop の初回起動と compose 検証(Phase 1 で構成作成、検証は Docker 起動後)
- ffmpeg/docker は PATH 未反映。設定のパス解決(D-007)で吸収する
- ADR-0005 の reconcile 実装は Phase 5(Fake で E2E 検証)

## 完了条件

仕様§23の20項目。詳細は docs/architecture.md 参照。
