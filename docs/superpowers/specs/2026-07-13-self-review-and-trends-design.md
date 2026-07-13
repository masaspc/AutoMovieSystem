# 設計: 投稿後セルフレビュー改善ループ + トレンド即応動画化

日付: 2026-07-13 / 承認: ユーザー確認済み(RSS方式・自動反映・ワンクリック一括制作) / 決定: D-026

## 目的

1. **セルフレビュー改善ループ**: 投稿後の反応(指標・維持率・コメント)からLLMが
   動画の良かった点・悪かった点を抽出し、改善指示を次回の台本生成へ自動反映する。
2. **トレンド即応動画化**: RSSフィードから最新情報(AIニュース等)を収集し、
   記事1件をワンクリックで「ニュース解説Short」として自動レビューまで一括制作する。

いずれも既存の絶対原則を変更しない: Provider抽象化(テストはFake)、冪等、
予算管理(UsageRecord)、人間承認・private投稿のfail-closedゲート不変、
本文転載なし(見出し+リンク+短い要約のみ使用し、動画は独自解説を生成)。

## 機能1: セルフレビュー改善ループ

### データフロー

```
Publication(投稿済み)
  → 入力収集: VideoMetricDaily(再生/CTR/維持率) + 場面別維持率(retention分析) + コメント分類集計
  → LLMセルフレビュー(call_llm, operation="self_review", model_policy="mid")
  → Insight保存(insight_type="self_review", source_type="publication")
  → 次回台本生成プロンプトへ「過去動画の振り返りからの改善指示」を自動注入
```

### コンポーネント

- `app/services/feedback/self_review.py`(新規)
  - `run_self_review(session, *, publication_id, provider) -> list[Insight]`
  - 構造化出力スキーマ `SelfReviewReport`:
    `good_points: list[str]` / `bad_points: list[str]` /
    `lessons: list[{finding: str, recommended_action: str}]`(最大5件)
  - 冪等キー: `self_review:{publication_id}:{最新指標日}`
    (新しい日の指標が入れば再レビュー可。同日重複はJobRunがスキップ)
  - lessonsをInsight行として保存(findingとrecommended_actionをそのままマップ)
- `collect_recent_lessons(session, channel_id, limit=5)`(同モジュール)
  - チャンネル配下のself_review Insightsを新しい順に取得
  - `app/services/scripts/generator.py` の `_build_prompts` がこのブロックを
    システムプロンプト末尾へ追加:「【過去動画の振り返りからの改善指示(必ず反映)】…」
  - Insightを改善提案画面で削除すれば注入対象から外れる(自動反映のオプトアウト)
  - PROMPT_VERSIONは変更しない(プロンプト内容の変動はLLMCacheのハッシュで自然に分離。
    既存topicの冪等キーは不変=勝手に再生成しない)

### トリガー

1. 手動: `/publications` 一覧・動画プロジェクト詳細に「自己レビューを実行」ボタン
   → Celeryタスク `feedback.run_self_review`(進捗バナー対応)
2. 自動: Celery beat 日次 `feedback.run_daily_self_reviews`
   — 前日以降の指標があるPublicationを走査して未レビュー分を実行
   (冪等キーにより二重実行なし。LLMコストは1動画/日1回が上限)

## 機能2: トレンド即応動画化

### データフロー

```
RSS/Atomフィード(TREND_FEED_URLS)
  → /trends 一覧(見出し・出典・要約200字・公開時刻)
  → 「⚡即動画化(Short)」1クリック
  → Topic(source_type="trend", source_refにURLハッシュ=重複防止)
    + Evidence(source_url=記事URL, claim=要約, verification_status="pending")
    + VideoProject(production_settings: preset="short",
      script_template="news_commentary", bgm_mood="serious")
  → 既存 produce_video_task(台本→素材→音声→レンダリング→自動レビュー)
  → 承認・投稿は従来通り人間(fail-closed不変)
```

### コンポーネント

- `app/providers/trends/`(新規)
  - `base.py`: `TrendItem(title, url, source, summary, published_at)` +
    `TrendProvider` Protocol(`async fetch_latest(limit) -> list[TrendItem]`)
  - `fake.py`: 決定的な3件を返すFake(テスト・デモ用)
  - `rss.py`: httpx + 標準ライブラリ `xml.etree` でRSS2.0/Atomを解析。
    取得は title / link / description(タグ除去・200字) / pubDate のみ。
    フィード単位でfail-soft(到達不可・不正XMLはログ警告してスキップ)
  - `factory.py`: `TREND_PROVIDER=fake|rss`(既定fake=テストでネットワーク不使用)
- 設定(`app/core/config.py` / `.env.example`):
  `TREND_PROVIDER` / `TREND_FEED_URLS`(カンマ区切り) / `TREND_FETCH_LIMIT`(既定20)
- `app/services/trends/service.py`(新規)
  - `instant_videoize(session, *, channel_id, title, url, summary, source) -> Topic`
  - 重複防止: `Topic.source_ref = "trend:" + sha256(url)[:16]` を既存Topicと照合し、
    既存なら既存Topicを返す(再ボタン押下は既存の冪等パイプラインが吸収)
  - orchestrationの既存ヘルパーで RESEARCH_READY まで前進+設定付きVideoProject作成
- `app/web/trends_page.py`(新規、ナビ「グロース」配下に「トレンド」)
  - GET `/trends`: providerから最新記事を取得して一覧表示
  - POST `/trends/videoize`: フォーム(hidden: title/url/summary/source)→
    `instant_videoize` → `produce_video_task.delay(topic_id)` →
    企画詳細へリダイレクト(進捗バナー)

### 権利面のガード(コンテンツポリシー準拠)

- フィードは配信目的で公開されているRSS/Atomのみ。本文は取得・保存しない
- 動画はLLMが独自の解説台本を生成(news_commentaryテンプレート=事実と意見を区別)し、
  概要欄・Evidenceに出典URLを明記

## エラー処理

- RSS: フィード単位でスキップ+`logger.warning`。全滅時は空一覧+画面に案内
- セルフレビュー: 指標ゼロのPublicationはスキップ(前提条件エラーにしない)。
  LLMスキーマ不正は既存のllm_gateway修復リトライに委ねる
- 一括制作の失敗: 既存どおりJobRun失敗記録+ダッシュボード「今日の運用」の失敗中へ表示

## テスト

- RSSパーサ: フィクスチャXML文字列(RSS2.0/Atom/不正XML)で検証。ネットワーク不使用
- trends service: Fake provider + in-memory DB で Topic/Evidence/設定付きProject生成と
  URL重複防止を検証
- self_review: Fake LLM(専用ジェネレーター追加)で Insight保存・冪等キー・
  指標なしスキップを検証
- lessons注入: self_review Insightがあるチャンネルでプロンプトに改善指示ブロックが
  含まれることを検証
- web: /trends 表示、videoize POST(dispatchモック)、自己レビューボタン

## ドキュメント更新

- `.env.example`(TREND_*)、`TASKS.md`、`DECISIONS.md`(D-026)、`docs/operations.md`
