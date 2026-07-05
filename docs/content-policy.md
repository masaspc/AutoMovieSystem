# コンテンツポリシー・編集方針

本システムが実装する制約・ガイドライン・自動公開の条件。

## 基本原則

### 1. 人為的なエンゲージメント操作を禁止

YouTube の利用規約に違反するため以下は**実装しない**:

- 再生数の人為的増加(ボット・サービス利用)
- 登録者数の人為的増加
- 高評価・低評価の意図的操作
- コメントの意図的削除・非表示
- 他チャンネル動画への不正コメント

**実装内容**: 統計収集・コメント同期・Insight生成は**受信側のみ**(監視)。
操作は一切なし。

### 2. 無断転載・著作権侵害の禁止

- 著作権者の許可なしに他者の著作物を使用できない
- 出典記載なしの引用禁止
- 既公開動画の転載禁止

**実装内容**:
- Evidence テーブルで出典(source_url等)を必ず記録
- Topic 作成時に source_type / source_ref で由来を自動記録(CSV ハッシュ・コメント ID等)

### 3. AI生成コンテンツの開示

YouTube のポリシー変更(2023-)に準拠:

- LLM生成台本・TTS音声・AI画像を使用した動画は「AI生成コンテンツ含む」を明示
- 視聴者が AI 関与を認識できるように disclosure

**実装内容**:
- VideoProject の `contains_synthetic_media` flag (デフォルト true)
- YouTube description に "This video contains AI-generated content" を自動付記(実装時)

### 4. 児童向けコンテンツの明示

- 児童向けか成人向けかを明確に指定
- YouTube の COPPA(米国児童プライバシー法)遵守

**実装内容**:
- Channel.audience_age_group = "children" / "general" / "adults"
- VideoProject.youtube_content_rating で自動指定

### 5. 高リスク領域の自動公開禁止

以下の領域は自動公開不可(人間承認 + 追加チェック必須):

| 領域 | 理由 | チェック |
|---|---|---|
| **投資** | 金融アドバイスの監規制 | REVIEW_HIGH_RISK_KEYWORDS に "投資" |
| **医療** | 医療アドバイスの法的リスク | "医療" |
| **法律** | 法的アドバイスの責任 | "法律" |
| **セキュリティ** | 悪用リスク | "セキュリティ" |

**実装**:
```python
# app/services/reviews/gate.py
if any(keyword in content for keyword in HIGH_RISK_KEYWORDS):
    can_auto_publish = False  # 人間承認後も public にしない
```

`.env` で キーワード追加可:
```env
REVIEW_HIGH_RISK_KEYWORDS=投資,医療,法律,セキュリティ,仮想通貨
```

## 自動公開のゲート (6条件)

fail-closed 原則。1つでも欠ければ public に**しない**(private 保留)。

### ゲート条件

#### 1. 自動レビュー合格

`Review.blocking_findings == 0`

チェック内容(app/services/reviews/machine.py + content.py):

**機械検査**:
- Duration 許容範囲(85%〜115% of target_duration)
- 解像度 >= 1920x1080
- コーデック映像 H.264 / VP9 / H.265
- コーデック音声 AAC / Opus
- 音量 -30dB 〜 -5dB
- 沈黙連続 < 10 秒

**LLMコンテンツ検査**:
- 高リスクキーワード検出(投資・医療・法律・セキュリティ)
- 違法・危険コンテンツ判定

#### 2. 人間承認あり

`Approval.decision == "approved"`

REQUIRE_HUMAN_APPROVAL=true の場合必須。

#### 3. ファイルチェックサム一致

アップロード後、動画ファイル内容が変更されていないか確認。

```python
checksum_before_upload = sha256(video_bytes)
# ... upload ...
checksum_after = sha256(downloaded_bytes)
assert checksum_before_upload == checksum_after
```

#### 4. メタデータ確定

- Title 確定
- Description 確定
- Tags 確定
- Thumbnail 選択完了
- Category 選択完了

#### 5. 重複 youtube_video_id なし

同じ動画を複数回投稿(アップロード)しない:

```python
existing = session.query(Publication).filter(
    Publication.youtube_video_id == new_youtube_video_id
).one_or_none()

if existing is not None and existing.id != current_publication.id:
    can_auto_publish = False  # 重複 → 拒否
```

**Reconcile ロジック(ADR-0005)**:
- 同じ idempotency_key で再アップロード時、description 内のマーカー検索
- 既にアップロード済みなら youtube_video_id を復元(二重投稿防止)

#### 6. 有効な OAuth 認証

YouTube API へのアクセストークン有効期限未切れ:

```python
# app/providers/youtube/real.py
oauth_token = session.query(OAuthToken).first()
if oauth_token is None or oauth_token.is_expired():
    can_auto_publish = False
```

### 自動公開フロー

```
VideoProject status = UPLOADED_PRIVATE
  ↓
schedule_video_for_publish() 呼び出し(API / Celery)
  ↓
can_auto_publish() が 6条件確認
  ↓
全て ✓ かつ AUTO_PUBLISH_ENABLED=true
  ↓
YouTubeProvider.update_video_visibility(video_id, visibility="public")
  ↓
Publication.published_at = now()
  ↓
status → PUBLISHED
```

AUTO_PUBLISH_ENABLED=false なら、この手順は自動実行されず、
管理画面で人間が明示的に "今すぐ公開" を選択する。

## 出典管理 (Evidence)

企画の根拠を記録:

```python
class Evidence(Base):
    id: str
    topic_id: str
    source_type: str  # "web_research" / "youtube_comment" / "csv_import"
    source_url: str | None  # URL がある場合
    source_ref: str  # CSV行ハッシュなど、トレーサビリティのための参照
    collected_at: datetime
```

**実装の冪等性**:
- source_type + source_ref の組み合わせで重複判定
- CSV インポート時、行ハッシュから自動生成(複数実行しても重複なし)

## AI生成の開示

### contains_synthetic_media フラグ

```python
class VideoProject(Base):
    contains_synthetic_media: bool = True  # デフォルト true
```

**デフォルト true の根拠**:
- LLM台本生成 → AI関与
- TTS音声合成 → AI関与
- FFmpeg レンダリング → AI非関与だが、音声がAI
- → 総合的に synthetic と見なす

**false の場合**:
- すべてが人間作成(台本・ナレーション・画像)
- テンプレート・マクロのみ使用

### YouTube description への自動付記

```python
description = """
[本編]

...

---
This video contains AI-generated content. 
The script and voice-over were generated using LLM and TTS technologies.
"""
```

実装は Phase 7C で追加予定(MVP では仕様のみ)。

## コメント・批判への対応

### 自動分類

コメントを自動分類:

| 分類 | 特徴 |
|---|---|
| **APPRECIATION** | 肯定・賞賛(例: "Great!") |
| **QUESTION** | 質問・疑問 |
| **NEXT_TOPIC_REQUEST** | 次のテーマ提案 |
| **CRITICISM** | 建設的批判 |
| **SPAM** | スパム・無関係 |

**実装**: `app/services/comments/classifier.py` で LLM分類。

### Insight 生成

コメント由来の Insight:

```python
insight = Insight(
    publication_id=pub.id,
    category="comment_pattern",
    summary="NEXT_TOPIC_REQUEST が多い(3件)。視聴者は〇〇テーマへの関心が高い",
    recommended_topic="〇〇テーマの解説動画",
)
```

### 削除・非表示

ユーザー・YouTube による削除はシステムが追跡:

```python
# Comment.deletion_status で記録
class Comment(Base):
    deletion_status: str  # "visible" / "deleted" / "hidden"
```

定期同期で status 更新(ユーザー削除後、次の sync で反映)。

## 将来の拡張

### サブスクリプション機能

視聴者の有料登録制など MVP 外。

### コメント操作

コメントの自動削除・非表示・固定は実装しない(視聴者信頼損失)。

### リマーケティング広告

大規模展開時は Google Ads / YouTube 広告ネットワークを検討(政策別)。

## ポリシー違反時の対応

1. **YouTube による削除** → システムは状態追跡・Insight生成(原因分析)
2. **チャンネルペナルティ** → AUTO_PUBLISH_ENABLED を false に切り替え
3. **人間監視へ** → 全動画を manual approval 運用へ

本システムはポリシー強制の最後の砦ではなく、**人間の判断を支援する道具**。
最終責任は運用者にある。

## 関連ドキュメント

- **[docs/operations.md](operations.md)** - 高リスク領域検出時の対応
- **[docs/architecture.md](../docs/architecture.md)** - 公開ゲート(Section 12)
- **[DECISIONS.md](../DECISIONS.md)** - ADR-0005(reconcile詳細)
