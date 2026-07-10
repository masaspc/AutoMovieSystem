# ローカルLLM(OpenAI互換)構成ガイド

`LocalLLMProvider`(`app/providers/llm/local_openai.py`)は、Ollama / LM Studio / vLLM が
共通で提供する **OpenAI互換 Chat Completions API**(`POST {base_url}/chat/completions`)を
話す。API課金が発生しないため `estimated_cost_micro_usd` は常に `0` として記録される
(D-020)。

## 最短セットアップ(Windows + RTX 4090 + Docker Desktop)

Docker Desktopが起動済みなら、プロジェクト直下のPowerShellで次を一度だけ実行する。

```powershell
.\scripts\setup-local-ai.ps1
```

このスクリプトはOllamaの導入、`qwen3:32b` の取得、`.env` のローカルLLM設定、Docker Composeの
再構築、アプリとコンテナからOllamaへの接続確認までを行う。RTX 4090 (24GB VRAM) では32Bクラスを
第一候補としているが、初回ダウンロードには時間とディスク容量を要する。

軽量モデルから試す場合は、次を使う。

```powershell
.\scripts\setup-local-ai.ps1 -Model qwen3:8b
```

公式立ち絵をすでに必要な場所へ配置済みで、VOICEVOXも同時に有効化する場合は次を使う。
利用条件の確認と `normal.png` の配置が済んでいない場合、このオプションは安全のため停止する。

```powershell
.\scripts\setup-local-ai.ps1 -EnableCharacterVideo
```

つむぎを使わない2人掛け合いにする場合は、以下のように指定する。

```powershell
.\scripts\setup-local-ai.ps1 -EnableCharacterVideo -DialogueCast "zundamon,metan"
```

自動化できないのは、Docker Desktopの初回利用規約同意と、公式立ち絵の利用許諾確認・素材配置だけである。
完了後は `http://localhost:8000/dashboard` を開き、設定ページで `LLM_PROVIDER=local` を確認する。

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

## Ollama セットアップ手順

### Docker Desktopでこのアプリを動かす場合(Windows / Mac)

このプロジェクトの通常起動はDocker Composeである。OllamaはホストOSにインストールして
起動し、Dockerコンテナ内の `app` / `worker` から接続する。**コンテナから見た
`localhost` はホストOSではない**ため、`LOCAL_LLM_BASE_URL` に `localhost` は使わない。

1. [ollama.com](https://ollama.com/) からOllamaをインストールする。Windowsでは次でもよい。

   ```powershell
   winget install Ollama.Ollama
   ```

2. PowerShellでモデルを取得する。最初は軽量な `qwen3:8b` を推奨する。

   ```powershell
   ollama pull qwen3:8b
   ```

3. Ollamaが起動していることを確認する。

   ```powershell
   ollama list
   Invoke-WebRequest -UseBasicParsing http://localhost:11434/api/tags
   ```

4. プロジェクト直下の `.env` に以下を設定する。`host.docker.internal` はDocker Desktopから
   ホストOSへ接続するための名前である。

   ```env
   LLM_PROVIDER=local
   LOCAL_LLM_BASE_URL=http://host.docker.internal:11434/v1
   LOCAL_LLM_API_KEY=
   LOCAL_LLM_MODEL_LOW=qwen3:8b
   LOCAL_LLM_MODEL_MID=qwen3:8b
   LOCAL_LLM_MODEL_HIGH=qwen3:8b
   ```

   ずんだもん・四国めたんの動画を作る場合も、台本と企画の生成はこの設定に従う。
   VOICEVOXの設定とは独立しているため、`TTS_PROVIDER=voicevox` はそのまま併用できる。

5. 設定をコンテナへ反映する。

   ```powershell
   .\scripts\dev.ps1 up
   ```

6. ブラウザで `http://localhost:8000/settings` を開き、`LLM_PROVIDER` が `local` であることを
   確認する。続けて通常どおり企画、台本、動画プロジェクトを作成する。

### アプリをDockerなしで直接起動する場合

`uv run uvicorn ...` のようにアプリ自身もホストOSで起動する場合だけ、次の設定を使う。

```env
LLM_PROVIDER=local
LOCAL_LLM_BASE_URL=http://localhost:11434/v1
LOCAL_LLM_API_KEY=
LOCAL_LLM_MODEL_LOW=qwen3:8b
LOCAL_LLM_MODEL_MID=qwen3:8b
LOCAL_LLM_MODEL_HIGH=qwen3:8b
```

LM Studio を使う場合は「Local Server」機能を有効化し、ポートに応じて
`LOCAL_LLM_BASE_URL` を設定する。Docker DesktopでLM StudioをホストOSに起動する場合は、
たとえば `http://host.docker.internal:1234/v1` となる。vLLM の場合は `--api-key` を
指定していれば `LOCAL_LLM_API_KEY` にも設定する。

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

LOCAL_LLM_BASE_URL=http://host.docker.internal:11434/v1
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

## よくある接続エラー

| 症状 | 確認・対処 |
|---|---|
| `Connection refused` / タイムアウト | OllamaがホストOSで起動しているか、`Invoke-WebRequest http://localhost:11434/api/tags` で確認する。Docker運用では `LOCAL_LLM_BASE_URL` が `host.docker.internal:11434/v1` になっているか確認する。 |
| `model not found` | `ollama list` でモデル名を確認し、`.env` の `LOCAL_LLM_MODEL_LOW` / `MID` / `HIGH` を一致させる。必要なら `ollama pull <モデル名>` を実行する。 |
| 台本生成が遅い / タイムアウト | まず `qwen3:8b` など小さいモデルを3ポリシーに設定する。必要に応じて `LOCAL_LLM_TIMEOUT_SECONDS` を延長する。 |
| 構造化出力のエラー | 小さいモデルではJSON形式が崩れることがある。再試行しても続く場合は、より性能の高いモデルに変えるか、人間確認を前提にFake/別プロバイダーで動作を切り分ける。 |

## 関連ドキュメント

- **[docs/cost-control.md](cost-control.md)** — AI予算・UsageRecord・料金表
- **[DECISIONS.md](../DECISIONS.md)** — D-006(実APIキー未設定でも全機能デモ可能)、
  D-020(本ドキュメントの決定事項)
