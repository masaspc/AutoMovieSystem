# AI予算・コスト管理・料金表

LLM/TTS の API 呼び出しコスト制御。

## 概要

| コンポーネント | 記録先 | 制御 | デフォルト |
|---|---|---|---|
| **UsageRecord** | `usage_records` テーブル | 全 LLM/TTS 呼び出しを記録 | 自動 |
| **BudgetLedger** | `budget_ledger` テーブル | 日次・月次予算の予約・確定・解放 | 自動 |
| **予算警告** | `/usage` 画面・ログ | 80% で ⚠️ 警告 | 自動 |
| **予算停止** | AI タスク保留キュー | 100% で AI 処理停止 | 自動 |

## UsageRecord: 呼び出し記録

### スキーマ

```python
class UsageRecord(Base):
    id: str
    job_run_id: str | None  # どのジョブから呼ばれたか
    seq: int  # job_run_id 内での通し番号(UNIQUE 制約)
    
    provider: str  # "anthropic" / "generic_tts"
    model: str  # "claude-haiku-4-5-20251001" / "voicevox"
    operation: str  # "generate_script" / "classify_comments" / "synthesize_audio"
    
    input_tokens: int | None  # LLM のみ
    output_tokens: int | None  # LLM のみ
    input_characters: int | None  # TTS のみ
    
    # 料金(マイクロUSD。1 USD = 1_000_000)
    estimated_cost_micro_usd: int
    
    cached: bool  # キャッシュヒット時 true(実コスト=0)
    created_at: datetime
    
    # Ledger との紐付け
    budget_ledger_id: str | None
```

### 記録タイミング

ジョブ完了と同一トランザクション:

```python
# app/services/topics/scorer.py (例)
def score_topic(topic_id: str, *, session: Session) -> None:
    # ... スコア計算(LLM呼び出し) ...
    
    # JobRun 成功と同一トランザクション内で UsageRecord 記録
    session.add(UsageRecord(
        job_run_id=job_run.id,
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        operation="score_topic",
        input_tokens=1234,
        output_tokens=567,
        estimated_cost_micro_usd=2345,  # キャッシュヒットなら 0
        cached=False,
    ))
    session.commit()
```

**利点**:
- ジョブ失敗 → トランザクション rollback → UsageRecord も削除(二重計上防止)
- キャッシュ効率追跡可能

## BudgetLedger: 予算管理

### スキーマ

```python
class BudgetLedger(Base):
    id: str
    
    # どの予算か
    scope: str  # "daily" / "monthly"
    period_start: date  # "2026-07-05" (daily) / "2026-07-01" (monthly)
    
    # 金額(マイクロUSD)
    total_budget_micro_usd: int  # 設定値
    reserved_micro_usd: int  # 使用予約済み
    committed_micro_usd: int  # 実際に使用確定
    released_micro_usd: int  # キャンセルで返却
    
    # available = total - reserved - committed
    # (released は committed に含まれる)
```

### 操作フロー: reserve → commit / release

```
[ LLM/TTS 前 ]
  ↓
estimate_cost() → 推定コスト算出
  ↓
budget.reserve(estimated_cost)
  → BudgetLedger.reserved += estimated_cost
  → available 減
  ↓
[ LLM/TTS 実行 ]
  ↓
成功した場合:
  budget.commit(actual_cost)
    → BudgetLedger.reserved -= estimated_cost
    → BudgetLedger.committed += actual_cost
  ↓
失敗した場合:
  budget.release(estimated_cost)
    → BudgetLedger.reserved -= estimated_cost
    (committed 増加なし)
```

### 例: generate_script

```python
# app/services/scripts/generator.py
def generate_script(topic_id: str, *, session: Session) -> Script:
    topic = session.query(Topic).get(topic_id)
    budget = BudgetManager(session)
    
    # 推定コスト(claude-sonnet: $3/1M input, $15/1M output)
    estimated_cost = estimate_script_generation_cost(topic)
    
    # 予算確保
    if not budget.reserve(estimated_cost):
        raise BudgetExceededError(
            f"Cannot reserve {estimated_cost} micro USD. "
            f"Available: {budget.available()}"
        )
    
    try:
        # LLM 呼び出し
        response = anthropic_provider.generate(prompt)
        actual_cost = calculate_actual_cost(response)
        
        # 確定
        budget.commit(actual_cost)
        
        # UsageRecord 記録
        session.add(UsageRecord(...))
        session.commit()
        
    except Exception as exc:
        # 返却
        budget.release(estimated_cost)
        raise
```

**利点**:
- check-then-act 競合回避(SQLite/PG 両対応の条件付き UPDATE)
- 予約で「上限到達前に処理停止」が可能
- オーバーランなし

## 料金表

コード内定数。更新時はコード変更が必要(MVP 許容)。

### LLM (Anthropic)

`app/core/costs.py`:

```python
MODEL_PRICING = {
    # input_per_1m_tokens (USD), output_per_1m_tokens (USD)
    "claude-haiku-4-5-20251001": (0.80, 4.00),          # low
    "claude-sonnet-5": (3.00, 15.00),                   # mid
    "claude-opus-4-8": (15.00, 75.00),                  # high
}

# 実装は micro USD:
# 0.80 USD → 800_000 micro USD
```

### TTS (Generic Command)

`app/services/media/tts.py`:

```python
# 仮定: 文字あたり $0.0001 (実際のサービス料金による)
COST_PER_CHARACTER = 100  # micro USD
```

### 計算例

#### generate_script

- Input: 2000 tokens (topic description)
- Output: 1000 tokens (script)
- Model: claude-sonnet-5
- Cost: `(2000 * 3 / 1_000_000 + 1000 * 15 / 1_000_000) * 1_000_000`
  = `6 + 15 = 21 micro USD ≈ $0.000021`

#### synthesize_audio

- Text: 500 characters
- Cost: `500 * 100 = 50_000 micro USD = $0.05`

#### score_topic

- Input: 1500 tokens (topic + 企画全文)
- Output: 500 tokens (JSON scores)
- Model: claude-haiku-4-5 (low)
- Cost: `(1500 * 0.8 + 500 * 4) / 1_000_000 * 1_000_000`
  = `1.2 + 2 = 3.2 micro USD ≈ $0.0000032`

## operation 別上限

`app/core/costs.py`:

```python
OPERATION_LIMITS = {
    "score_topic": 100_000,              # 100 topics × $0.001 / topic = $0.10
    "generate_script": 500_000,          # 1 project × $0.50 ≈ claude-sonnet
    "classify_comments": 50_000,         # ~100 comments
    "synthesize_audio": 100_000,         # 1000 chars × $0.10
    "review_content_lm": 200_000,        # Final check
}

# 制約: operation.reserve() が上限超過なら fail
budget.reserve(cost, operation="generate_script")
  # OPERATION_LIMITS["generate_script"] を超えたら BudgetExceededError
```

## デフォルト設定

`.env`:

```env
# 日次: $5.00
DAILY_AI_BUDGET_MICRO_USD=5000000

# 月次: $100.00
MONTHLY_AI_BUDGET_MICRO_USD=100000000
```

### 予算別シナリオ

| シナリオ | 日次 | 月次 | 用途 |
|---|---|---|---|
| **開発(Fake)** | $5 | $100 | Fake で実API未呼び出し |
| **MVP検証** | $10 | $300 | 10 topics/day × $1/topic ≈ |
| **運用開始** | $20 | $600 | 20 topics/day, comments 同期 |
| **スケール** | $100+ | $3000+ | 複数チャンネル並行 |

## 警告・停止の挙動

### 80% 警告

日次 / 月次の 80% に到達:

```python
# app/services/costs/budget.py
available = budget.available()
total = budget.total()

if available / total < 0.2:
    logger.warning(
        "budget_near_limit",
        budget_type="daily",
        remaining_percent=available / total * 100,
        remaining_micro_usd=available,
    )
    # 管理画面 /usage で ⚠️ 表示
```

**対応**: `/usage` で確認し、予算調整またはスケジュール調整。

### 100% 停止

日次 / 月次の 100% 消費:

```python
# app/services/costs/budget.py
def reserve(cost: int, operation: str) -> bool:
    available = budget.available()
    if cost > available:
        logger.error("budget_exceeded", available=available, requested=cost)
        return False  # reserved せず False 返す

# app/services/scripts/generator.py
if not budget.reserve(estimated_cost):
    raise BudgetExceededError("...")
    # → API 400 Bad Request / Celery タスク失敗 → `/jobs` に保留
```

**AI 処理**: generate_script / classify_comments / synthesize_audio 停止

**非AI 処理**: 継続
- YouTube 投稿 / アップロード
- 指標同期
- コメント取得
- 管理画面操作

**対応**:
1. `.env` で月次予算を増額 → アプリ再起動
2. または月初リセット待機
3. または古い UsageRecord を DELETE(運用判断)

## LLM キャッシュ

`app/services/topics/cache.py`:

```python
class LLMCache:
    def get(self, operation: str, input_hash: str) -> str | None:
        """(operation, input_hash) をキーに キャッシュ応答を返す"""
        record = session.query(LLMCacheRecord).filter(
            LLMCacheRecord.operation == operation,
            LLMCacheRecord.input_hash == input_hash,
        ).one_or_none()
        return record.response if record else None
    
    def set(self, operation: str, input_hash: str, response: str) -> None:
        """レスポンスをキャッシュ"""
        session.add(LLMCacheRecord(
            operation=operation,
            input_hash=sha256(input_text),
            response=response,
            expires_at=now() + timedelta(days=30),
        ))
```

**例**:
```
[ 初回 ]
score_topic(topic_id=1)
  input_hash = hash("Topic: Data science...")
  cache miss
  LLM 呼び出し → $0.003
  캐시 저장

[ 2回目、同じ企画スコア再計算 ]
score_topic(topic_id=1)  
  input_hash = hash("Topic: Data science...")
  캐시 hit
  LLM 呼び출し 없음 → $0 (비용 절감)
  UsageRecord(cached=true, cost=0)
```

**コスト削減効果**: 企画スコアの再計算・テスト時に費用ゼロ。

## モデルルーティングポリシー

operation に応じて自動選択:

```python
MODEL_POLICY = {
    "score_topic": "low",                  # claude-haiku
    "classify_comments": "low",
    "generate_script": "mid",              # claude-sonnet
    "review_content_lm": "high",           # claude-opus
}
```

### ルーティング判断

```python
# app/providers/llm/real.py
def choose_model(operation: str, *, budget: BudgetManager) -> str:
    policy = MODEL_POLICY.get(operation, "low")
    
    if policy == "low":
        return "claude-haiku-4-5-20251001"
    elif policy == "mid":
        return "claude-sonnet-5"
    elif policy == "high":
        return "claude-opus-4-8"
    else:
        # Fallback
        return "claude-haiku-4-5-20251001"
```

**昇格なし**: operation の上限超過時、高価なモデルに自動昇格しない。
保留してレビュー対象。

## 定期メンテナンス

### 日次確認

```powershell
# /usage 画面で確認
# または DB クエリ
uv run python -c "
from app.db.session import SessionLocal
from app.models.usage_record import UsageRecord
import datetime as dt

session = SessionLocal()
today = dt.date.today()
records = session.query(UsageRecord).filter(
    UsageRecord.created_at >= dt.datetime.combine(today, dt.time.min)
).all()

total_cost = sum(r.estimated_cost_micro_usd for r in records)
print(f'Today cost: ${total_cost / 1_000_000:.2f}')
"
```

### 月次レポート

```python
# app/services/costs/reporting.py (将来実装)
def monthly_cost_summary(month: int, year: int) -> dict:
    """月別コスト集計"""
    return {
        "total_cost_micro_usd": ...,
        "by_operation": {...},
        "by_model": {...},
        "cache_hit_rate": ...,
    }
```

## 関連ドキュメント

- **[docs/operations.md](operations.md)** - 予算100%時の対応
- **[docs/architecture.md](../docs/architecture.md)** - コスト制御(Section 8)
- **[DECISIONS.md](../DECISIONS.md)** - ADR-0007(整数マイクロUSD)
