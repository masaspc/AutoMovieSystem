# TASKS

進行状況の正本。各Phase終了時に更新する。

## 状態

- [x] Phase -1: 環境セットアップ(Python 3.12 via uv ✅ / FFmpeg via winget ✅ / Docker Desktop インストール中)
- [ ] Phase 0: 足場 + Claude Code設定(進行中)
- [ ] Phase 1: 基盤(FastAPI/DB/Alembic/Celery/Docker/CI)
- [ ] Phase 2: 企画・台本・コスト管理
- [ ] Phase 3: 動画生成
- [ ] Phase 4: 自動レビュー + 承認
- [ ] Phase 5: YouTube投稿
- [ ] Phase 6: 分析・コメント・フィードバック
- [ ] Phase 7: 管理画面 + 運用
- [ ] Phase 8: 総合検証

## Phase 0 チェックリスト

- [x] CLAUDE.md
- [ ] .claude/agents/ 9体
- [ ] .claude/skills/ 4件
- [ ] DECISIONS.md
- [ ] docs/architecture.md(状態遷移図・データフロー)
- [ ] docs/adr/ 初期ADR
- [ ] .gitignore
- [ ] architect による最小設計レビュー

## 未解決事項

- Docker Desktop のインストール完了確認と初回起動(管理者権限/再起動が必要ならユーザーへ通知し、SQLite+eager で先行)

## 完了条件

仕様§23の20項目。詳細は docs/architecture.md 参照。
