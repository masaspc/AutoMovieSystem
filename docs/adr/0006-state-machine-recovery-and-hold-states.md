# ADR-0006: 状態機械の復旧エッジ・終端/却下・予算ホールド状態

Status: Accepted (2026-07-04) — 主任(Fable 5)承認済み

## Context
docs/architecture.md の遷移表は正常系を順方向一本道、失敗状態を列挙するのみで、以下が未定義:
1. **復旧エッジ**: 「失敗状態→処理中の正常系へ戻す」とあるが、各失敗状態がどの正常状態へ
   戻るか(RENDER_FAILED → ASSETS_READY 等)の写像が遷移表に無い。実装が曖昧になる。
2. **人間却下の行き先**: 承認ゲートで人間が却下した場合の状態が無い。
3. **終端の分岐欠如**: AUTO_PUBLISH_ENABLED=false(既定)では UPLOADED_PRIVATE が終端に
   なり得るが、遷移表は UPLOADED_PRIVATE → SCHEDULED → PUBLISHED を一本道で強制している。
4. **予算ホールドの表現不能**: コスト制御は 100% 超で AI 処理を「保留キュー」へ回すが、
   保留中の VideoProject を表す状態が無く、進行中と区別できない(ADR-0007 と連動)。

## Decision
- 遷移表に**復旧エッジを明示的に列挙**する(失敗状態 → 対応する直前正常状態のみ、飛び越え禁止は維持)。
- `REJECTED`(人間却下・監査ログ decided_by 必須)を追加。HUMAN_APPROVED ゲートからの分岐先とする。
- UPLOADED_PRIVATE を**準終端**とし、SCHEDULED/PUBLISHED への遷移は公開ゲート全通過 +
  AUTO_PUBLISH_ENABLED=true のときのみ許可。それ以外は private 保留を正常終端として認める。
- `BUDGET_HELD`(または保留フラグ + retriable_at)を導入し、予算超過で AI ジョブが保留された
  プロジェクトを表現。予算リセット時に beat が再スキャンして正常系へ戻す。
- 定期同期(sync_metrics / sync_comments)は status 値に結合させず、PUBLISHED 以降の
  Publication を対象にした述語で駆動する(FEEDBACK_GENERATED が終端でも同期継続可能に)。

## Consequences
- 再実行・失敗復旧・却下・予算保留の全パスが遷移表で機械検証可能になる。
- 状態数が増える。state_machine の遷移表テストで全エッジを網羅する。
