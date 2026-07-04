---
name: implement-feature
description: 機能実装の標準手順。新しいサービス・モデル・ジョブ・画面を追加するとき、実装サブエージェントへ依頼を組み立てるときに使用する。
---

# 機能実装手順

## 1. 依頼の組み立て(主任)

サブエージェントへ渡すのは以下だけ。リポジトリ全体を読ませない。

- 対象機能の1段落説明
- 関連ファイルのパス(repo-explorer で特定済みのもの)
- 受入条件(テストで検証可能な形式)
- 守るべき制約(CLAUDE.md の該当項目を指名)

## 2. 実装順序(実装者)

1. 既存の類似実装を確認し、パターンを踏襲する
2. モデル変更がある場合: モデル → Alembicマイグレーション → `uv run alembic upgrade head` で検証
3. サービス層にロジック、workers/ は薄いラッパー、web/api はサービス呼び出しのみ
4. テストを書く(unit必須。DB絡みはintegration、プロバイダーはcontract)
5. `uv run pytest tests/unit <関連分>` → `uv run ruff check . --fix` → `uv run mypy app`

## 3. チェックリスト

- [ ] 状態遷移は state_machine 経由か
- [ ] ジョブに idempotency_key と JobRun 記録があるか
- [ ] LLM/TTS呼び出しに UsageRecord 記録があるか
- [ ] シークレットがログ・例外メッセージに漏れないか
- [ ] SQLite互換か(JSONB/ARRAY不使用)
- [ ] subprocess は引数配列か
- [ ] 外部入力のパス検証があるか

## 4. 報告

§2.5 の6項目形式(結論/変更ファイル/実行したテスト/テスト結果/残存リスク/主任が判断すべき事項)。
