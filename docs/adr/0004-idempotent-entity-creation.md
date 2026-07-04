# ADR-0004: エンティティ生成の冪等キー(Topic / VideoProject / UsageRecord)

Status: Accepted (2026-07-04) — 主任(Fable 5)承認済み

## Context
E2E要件は「全工程を2回実行して VideoProject / Publication / Comment / UsageRecord が
重複しないこと」。しかし現行設計(docs/architecture.md 冪等性設計・主要制約)が冪等キー/
一意制約を定義しているのは JobRun・Publication・Comment・VideoMetricDaily のみで、
**Topic と VideoProject の生成、および UsageRecord の記録には一意制約が無い**。
このままでは 2 回目実行で同一企画から VideoProject が二重生成され、要件を直接満たせない。

## Decision
- `Topic` に自然キー `(channel_id, source_kind, source_ref)` の UNIQUE を付与
  (手動企画は source_ref にクライアント生成 UUID、CSV/コメント派生は元IDを使用)。
  取り込みは get-or-create を UNIQUE 違反ハンドリングで実装する。
- `VideoProject` に `topic_id` の UNIQUE を付与(MVP は 1 Topic = 1 Project)。
  複数世代が必要になった時点で `(topic_id, generation)` UNIQUE へ拡張する。
- `UsageRecord` に `(job_run_id, seq)` UNIQUE を付与し、JobRun 成功と同一トランザクション
  で INSERT。JobRun が冪等スキップされれば UsageRecord も再挿入されない。
- E2E は「テーブル行数の重複ゼロ」に加え、`youtube_video_id` の値レベル重複ゼロも検証する
  (テーブル重複だけでは real パスの二重アップロードを検出できないため。ADR-0005 参照)。

## Consequences
- 企画取り込み・プロジェクト生成が競合下でも一意になり E2E 不変条件を保証できる。
- Topic の source_ref 設計を各企画ソース実装が守る必要がある(契約テスト対象)。
