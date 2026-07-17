# チャンネルプロファイル+金融チャンネル基盤 設計スペック

日付: 2026-07-18
状態: ユーザー承認済み(ブレインストーミング経由)

## 背景と目的

金融特化チャンネル(NISA/iDeCo)を新規開設し収益化する計画がある。
「他者より先へいける最新情報のキャッチアップ」が差別化の軸。

決定済みのコンテンツ方針: **制度解説(evergreen)+公的一次情報の速報**の両輪。
個別銘柄推奨・利回り予測・売買タイミング助言は一切しない
(金商法の投資助言該当性とYouTube収益化ポリシーの両面リスクを排除)。

動画スタイルは現行のずんだもん×つむぎ掛け合いを流用し、
チャンネル別のトーン設定のみ変える(追加素材コストゼロ)。

好機: 金融庁は公式RSSを配信しており(fsa.go.jp/kouhou/rss.html)、
2026年NISA制度改正(未成年拡大・対象商品拡大・枠復活の即年再投資)が進行中。
既存のトレンド即動画化基盤(D-026)がそのまま速報エンジンとして使える。

## コンポーネント設計

### 1. Channelモデル拡張(Alembicマイグレーション1本)

`channels.editorial_policy` JSON列(nullable)を追加。
Pydanticスキーマ `ChannelEditorialPolicy`(`app/schemas/editorial_policy.py` 新規):

```python
class ChannelEditorialPolicy(BaseModel):
    tone: str = ""                        # 例: 落ち着いた丁寧解説。専門用語は必ず言い換える
    target_audience: str = ""             # 例: 投資未経験の20〜40代
    prohibited_instructions: list[str] = []  # 台本プロンプトへ注入する禁止事項
    disclaimer_text: str = ""             # 免責文(概要欄+ED画面へ自動挿入)
    trend_feed_urls: list[str] = []       # チャンネル別RSSフィード
    default_production_settings: dict = {}  # 新規企画のProductionSettings既定値
```

SQLite互換のJSON列を使用(PG専用型は使わない)。既存Channelはpolicyなし(=全て現状動作)。

### 2. トレンドのチャンネル別化

- `TrendService` のフィード解決: 対象チャンネルの `trend_feed_urls` が非空ならそれを使用、
  空ならグローバル `TREND_FEED_URLS` にフォールバック
- `/trends` 画面は既存のチャンネル選択UIをそのまま活用(選択チャンネルのフィードで取得)
- 金融チャンネル用プリセット例を docs と `.env.example` のコメントに記載:
  金融庁公式RSS・厚労省新着・日経マネー系フィード等
- 取得内容は従来どおり見出し+リンク+200字要約のみ(全文スクレイピングしない。
  docs/content-policy.md 準拠)

### 3. 台本生成へのポリシー注入

- `generate_script` がTopicのChannelから `editorial_policy` を読み、
  tone / target_audience / prohibited_instructions をプロンプトの必須ブロックとして注入
  (スペックAの演出バリエーション注入と同居。注入順: ポリシー→演出指示)
- ポリシー内容はプロンプトの一部なので、既存の設定checksum付き冪等キーにより
  ポリシー変更時は台本が再生成される

### 4. 金融ガードレール(fail-closed 3層)

1. **プロンプト層**: `prohibited_instructions` に以下を金融チャンネルの既定として設定:
   - 個別銘柄・個別商品の推奨をしない
   - 利回り・値動きの断定/予測をしない
   - 売買タイミングの助言をしない
   - 制度・公的情報は出典(官庁名・発表日)を必ず言及する
2. **自動検査層**: コンテンツレビュー(automated review)に禁止表現ルールを追加:
   - 断定表現(「必ず儲かる」「絶対に上がる」「損しない」等)→ blocking
   - ルールはチャンネルポリシー非依存の共通実装とし、金融以外のチャンネルにも安全側に働く
   - 免責文の存在検証はアップロード前提検証(uploader)側で行う:
     disclaimer_text 非空のチャンネルでは、自動挿入後のdescriptionに免責文が
     含まれることを確認してからアップロードする(fail-closed)
3. **表示層**:
   - `disclaimer_text` を概要欄へ自動挿入(既存のクレジット自動追記と同じ重複防止パターン)
   - エンディングCTA画面の下部に免責一文を小さく表示(disclaimer_textが設定されている場合のみ)

既存の高リスクキーワードゲート(「投資」を含む)は変更しない。
REQUIRE_HUMAN_APPROVAL=true の人間承認運用も不変(fail-closed維持)。

### 5. 管理画面

- チャンネル設定画面(既存 /settings または channels 編集)に editorial_policy の編集フォームを追加
  (tone / target_audience / 禁止事項 / 免責文 / フィードURL / 既定制作設定)
- 金融チャンネル向けのポリシー設定例はdocsの手順書として提供する(投入ボタンは作らない。YAGNI)

## エラー処理

- editorial_policy が不正JSONの場合はデフォルト(ポリシーなし)として動作し、警告ログ(fail-soft)
- チャンネル別フィードの取得失敗は既存のフィード単位fail-softを踏襲

## テスト計画

- ChannelEditorialPolicy のバリデーション(空/部分/全項目)
- フィード解決のフォールバック(チャンネル別→グローバル)
- プロンプト注入(ポリシーあり/なしで台本プロンプトが変わる、禁止事項が含まれる)
- 禁止表現ルールの検査(断定表現でblocking、免責欠落でblocking)
- 概要欄への免責自動挿入(重複防止含む)
- マイグレーションupgrade/downgrade往復

## スコープ外

- 金融チャンネルのYouTube側開設・OAuth設定(運用作業)
- チャンネル別の演出プロファイル(スペックA参照。カタログは全チャンネル共通)
- 全文記事スクレイピング(方針として恒久的にしない)
- 個別銘柄データ・株価API連携(コンテンツ方針上不要)

## 実装順序

スペックA(演出バリエーションエンジン)→ 本スペックB。各々独立にテスト・コミット可能。
