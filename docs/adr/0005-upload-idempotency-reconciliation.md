# ADR-0005: アップロード2段階記録のクラッシュ整合(二重投稿防止)

Status: Accepted (2026-07-04) — 主任(Fable 5)承認済み

## Context
現行設計の 2 段階記録: (1) Publication を STARTED で INSERT → (2) YouTube アップロード
実行 → (3) youtube_video_id 記録 + COMPLETED。再試行時は STARTED の youtube_video_id
有無で再開判定し「動画チェックサム照会で二重を検出」とある。

問題:
- **(2)と(3)の間でクラッシュ**すると、YouTube 側に動画が生成済みなのに youtube_video_id
  が未記録の「孤児」になる。再試行は youtube_video_id=None を見て再アップロードし、
  **実 API で二重投稿**が起きる。Publication 行数は idempotency_key UNIQUE により 1 のままなので、
  行数ベースの E2E 重複チェックはこの二重投稿を**すり抜ける**。
- 復旧手段として挙げる「チェックサム照会」は **実 YouTube API に存在しない**(アップロード
  済み動画をコンテンツハッシュで検索する機能はない)。Fake では成立するが real パスは未解決。

## Decision
- アップロード直前(段階2の手前)に、idempotency_key を **description 末尾の不可視マーカー
  または動画メタの custom フィールド**として埋め込む。再試行時はチャンネルの直近アップロード
  一覧を list して当該マーカーで突合し、既存動画があれば再アップロードせず (3) の記録のみ行う
  (reconcile-before-upload)。
- 突合が確実でない(マーカー無しの旧孤児等)場合は fail-closed:自動再アップロードせず
  `UPLOAD_FAILED` にして人間の手動照合キューへ回す。
- Fake プロバイダーも同じ reconcile 契約(list + マーカー突合)を実装し、契約テストで real と
  同一挙動を担保する。docs/architecture.md の「チェックサム照会」記述はこの方式に差し替える。

## Consequences
- 実 API でも二重投稿を防げ、E2E は youtube_video_id 値の一意性で検証可能になる(ADR-0004)。
- YouTube quota を 1 回消費して list する。デフォルト private 運用のため孤児の影響は限定的。
