# YouTube自動投稿・レビュー・成長改善システム

YouTubeチャンネル向けの企画→台本→動画生成→レビュー→投稿→分析→フィードバックの一気通貫MVP。

## 技術スタック

- Python 3.12 / uv 管理(`uv sync`, `uv run <cmd>`)
- FastAPI + Jinja2 + HTMX(SPA不使用)
- SQLAlchemy 2 + Alembic + PostgreSQL(テストはSQLite互換 — PG専用型を使わない)
- Celery + Redis(ローカル検証は `CELERY_TASK_ALWAYS_EAGER=true` 切替可)
- FFmpeg / ffprobe(subprocess は必ず引数配列。`shell=True` 禁止)
- pytest / Ruff / mypy / pre-commit

## コマンド

Windows開発環境のため `make` と `scripts/dev.ps1` を併設。どちらも同じターゲット名。

```
make setup / up / down / migrate / seed / lint / typecheck
make test / test-unit / test-integration / test-e2e
make demo / security-check / clean-generated
```

PowerShell: `./scripts/dev.ps1 <target>`

## 絶対原則

- 外部API(YouTube/LLM/TTS)は全て `app/providers/` の Protocol 経由。テストで実APIを呼ばない(Fake実装を使う)
- デフォルト: `YOUTUBE_DEFAULT_PRIVACY_STATUS=private`, `AUTO_PUBLISH_ENABLED=false`, `REQUIRE_HUMAN_APPROVAL=true`。fail-closed
- APIキー・OAuthトークン・Cookie・個人情報をログに出さない(structlogのマスキングプロセッサ経由)
- 全ジョブは冪等(idempotency_key + チェックサム照合)。再実行で重複投稿・二重計上しない
- VideoProject の状態遷移は `app/services/state_machine.py` の遷移表のみ許可。飛び越え禁止
- 全LLM/TTS呼び出しは UsageRecord に記録。予算80%で警告、100%でAI処理停止(非AI処理は継続)
- 人為的な再生・登録・評価・コメントを発生させる機能、無断転載前提の機能は実装しない

## 構造

- `app/models/` SQLAlchemyモデル(§6の14エンティティ)
- `app/services/<domain>/` ドメインロジック(topics/scripts/media/reviews/publishing/analytics/comments/feedback/costs)
- `app/providers/<kind>/` 外部API抽象化(llm/tts/youtube/storage)— base.py に Protocol、fake.py に Fake実装
- `app/workers/` Celeryタスク(薄いラッパー。ロジックはservicesへ)
- `app/web/` 管理画面ルート、`app/api/` JSON API
- `tests/{unit,integration,contract,e2e}/`
- `generated/` 動画等の生成物(git管理外)

## 引継ぎ正本

- `TASKS.md` 進行状況、`DECISIONS.md` 決定事項、`docs/adr/` 設計判断
- Phase終了時に必ず両方を更新する
