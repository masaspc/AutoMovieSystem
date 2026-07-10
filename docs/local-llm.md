# ローカルLLM(OpenAI互換)構成ガイド

`LocalLLMProvider`(`app/providers/llm/local_openai.py`)は、Ollama / LM Studio / vLLM が
共通で提供する **OpenAI互換 Chat Completions API**(`POST {base_url}/chat/completions`)を
話す。API課金が発生しないため `estimated_cost_micro_usd` は常に `0` として記録される
(D-020)。

## 前提

- 外部APIプロバイダーと同様、`app/providers/llm/base.py` の `LLMProvider` Protocol に
  準拠する。呼び出し側は必ず `app/services/llm_gateway.py`(`call_llm`)経由で使うこと。
- テストでは実サーバーを一切呼ばない(httpxモック)。ローカル推論サーバーの起動有無に
  関わらず `uv run pytest` は常にグリーンになる。
- 構造化出力は `response_format={"type": "json_object"}` + システムプロンプトへの
  JSON Schema埋め込みで強制する。厳密なスキーマ適合はモデル性能に依存するため、
  不適合時は `app/services/llm_gateway.py` の修復リトライ(最大1回)に任せる。

## 推奨ハードウェア構成の目安

ローカルLLMの実行可否・速度はVRAM/統合メモリ容量に強く依存する。以下は目安であり、
実際の量子化形式・コンテキスト長によって必要メモリは変動する。

| ハードウェア | メモリ | 推奨モデルクラス | 量子化目安 |
|---|---|---|---|
| RTX 4090 | 24GB VRAM | 30Bクラス(例: Qwen3-32B) | Q4_K_M 前後 |
| Apple M5 Max | 128GB 統合メモリ | 70Bクラス(例: Llama 3.3 70B, Qwen2.5-72B) | Q4〜Q6 |

低VRAM環境では `LOCAL_LLM_MODEL_LOW`/`MID` に軽量モデル(8B前後)、余裕があれば
`HIGH` にも同クラスの量子化モデルを割り当てる。

## Ollama セットアップ手順(Windows/Mac共通の概略)

1. [ollama.com](https://ollama.com/) からインストール、または `winget install Ollama.Ollama`
2. モデルを取得: `ollama pull qwen3:8b` / `ollama pull qwen3:32b`
3. サーバーは既定で `http://localhost:11434` で待受(OpenAI互換パスは `/v1` 配下)
4. `.env` に以下を設定:

```env
LLM_PROVIDER=local
LOCAL_LLM_BASE_URL=http://localhost:11434/v1
LOCAL_LLM_API_KEY=
LOCAL_LLM_MODEL_LOW=qwen3:8b
LOCAL_LLM_MODEL_MID=qwen3:32b
LOCAL_LLM_MODEL_HIGH=qwen3:32b
```

LM Studio を使う場合は「Local Server」機能を有効化し、`LOCAL_LLM_BASE_URL` を
`http://localhost:1234/v1` に変更する(モデル名はLM Studioでロードしたモデルの識別子)。
vLLM の場合は `--api-key` を指定していれば `LOCAL_LLM_API_KEY` にも設定する。

## ポリシー別ルーティング(ハイブリッド構成)

`LLM_PROVIDER_LOW` / `LLM_PROVIDER_MID` / `LLM_PROVIDER_HIGH`(空文字なら `LLM_PROVIDER`
に従う)で `model_policy` ("low"/"mid"/"high") ごとに異なるプロバイダーを割り当てられる
(`app/providers/llm/factory.py` の `get_llm_provider`、実体は
`app/providers/llm/routing.py` の `RoutingLLMProvider`)。全ポリシーが同一プロバイダー名に
解決される場合は従来どおり単一プロバイダーが返るため、既存のシングルプロバイダー運用に
影響はない。

### 例: LOW/MIDはローカル、HIGH(公開前最終判定)はAnthropic

```env
LLM_PROVIDER=anthropic
LLM_PROVIDER_LOW=local
LLM_PROVIDER_MID=local
LLM_PROVIDER_HIGH=anthropic

LOCAL_LLM_BASE_URL=http://localhost:11434/v1
LOCAL_LLM_MODEL_LOW=qwen3:8b
LOCAL_LLM_MODEL_MID=qwen3:32b

ANTHROPIC_API_KEY=<your-api-key>
LLM_MODEL_HIGH=claude-opus-4-8
```

- `score_topic` / `classify_comment` 等の低リスク・高頻度operationはローカルで無料実行
- `review_content`(公開可否に関わる最終判定, 仕様§9)など高リスク判断は高性能モデルへ

## 注意点

- **公開前最終判定(仕様§9 の `high` ポリシー)はローカルの小型量子化モデルに任せず、
  高性能モデル(Anthropic等)または人間のレビューを推奨する。** ローカルモデルは
  構造化出力の厳密さ・有害表現検出の精度でクラウド最上位モデルに劣る場合がある。
  `REQUIRE_HUMAN_APPROVAL=true`(デフォルト)を維持していれば、最終的な公開可否は
  常に人間が確認するため、この点はfail-closedの多層防御の一部となる。
- ローカル推論は応答が遅いため `LOCAL_LLM_TIMEOUT_SECONDS`(デフォルト300秒)を環境に
  合わせて調整すること。
- `LOCAL_LLM_API_KEY` を設定しない場合は `Authorization` ヘッダー自体を送らない
  (Ollama/LM Studio は通常キー不要)。
- コストは常に `0` として `UsageRecord`/`BudgetLedger` に記録されるため、ローカル実行分は
  予算消費に影響しない(トークン数・レイテンシは引き続き記録される)。

## 関連ドキュメント

- **[docs/cost-control.md](cost-control.md)** — AI予算・UsageRecord・料金表
- **[DECISIONS.md](../DECISIONS.md)** — D-006(実APIキー未設定でも全機能デモ可能)、
  D-020(本ドキュメントの決定事項)
