---
name: verify-mvp
description: MVP完了条件(仕様§23の20項目)の検証手順。Phase 8および完了宣言の前に使用する。
---

# MVP検証手順

すべて実行し、結果(コマンド出力の要約)を添えて報告する。未実行項目を「動作します」と報告しない。

## 1. 静的検査

```
uv run ruff check .
uv run mypy app
```

## 2. テスト

```
uv run pytest tests/unit -q
uv run pytest tests/contract -q
uv run pytest tests/integration -q   # PG/Redis必要。なければCI結果を確認
uv run pytest tests/e2e -q
```

## 3. E2E冪等性(2回実行)

```
uv run pytest tests/e2e -q            # 1回目
uv run pytest tests/e2e -q            # 2回目(または専用の二重実行テスト)
```

確認: VideoProject / Publication / Comment / UsageRecord が重複していないこと。

## 4. デモ(Fakeのみで全工程)

```
./scripts/dev.ps1 demo   # または make demo
```

確認: 企画→スコア→台本→音声→動画(MP4実生成)→ffprobe検証→レビュー→承認→Fake投稿→統計/コメント→分類→Insight→次回企画候補。

## 5. Docker

```
docker compose up -d
docker compose exec app alembic upgrade head   # または migrate ターゲット
curl http://localhost:8000/health
```

管理画面 http://localhost:8000/dashboard の疎通確認。

## 6. セキュリティ

- security-reviewer の最終レビューで BLOCKER/HIGH = 0
- `git log --all -p | grep` 相当でシークレット混入なし(.env がgit管理外)

## 7. ドキュメント照合

- README の手順のみで第三者が起動できるか(docs-maintainer に実コマンドとの照合を依頼)
- docs/youtube-oauth.md に実OAuth設定手順があるか
