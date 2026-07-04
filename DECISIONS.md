# DECISIONS

採用した前提・決定事項の正本。番号付きで追記のみ(覆す場合は新番号で記録)。

## D-001: リポジトリルート直下に構築(2026-07-04)

仕様の `youtube-growth-automation/` サブディレクトリは作らず、git リポジトリルート
(`AutoMovieSystem/`)直下に構築する。ネストは import パスと CI 設定を複雑にするだけで利点がない。

## D-002: uv による Python 3.12 管理(2026-07-04)

システム Python が存在しない(MS Store スタブのみ)ため、uv で Python 3.12.13 を導入。
依存管理も uv(pyproject.toml + uv.lock)。実行は `uv run <cmd>`。

## D-003: SQLite 互換を維持した PostgreSQL 設計(2026-07-04)

本番・docker compose は PostgreSQL 16。単体/E2E テストの高速化と Docker 非依存の
ローカル検証のため、モデルは SQLite でも動く型のみ使用(JSONB でなく JSON、
ARRAY でなく JSON、サーバーサイド UUID でなくアプリ生成の文字列 UUID)。
PostgreSQL/Redis 前提の統合テストは `integration` マーカーで分離し、CI のサービス
コンテナと docker compose 環境で実行する。

## D-004: Celery eager モード切替(2026-07-04)

`CELERY_TASK_ALWAYS_EAGER=true` で Redis なしにジョブを同期実行できるようにする。
`make demo` と E2E テストはデフォルトで eager。compose 環境では通常の worker/beat。

## D-005: Windows 開発コマンドの二重化(2026-07-04)

Makefile(CI・Linux/Mac・Git Bash 用)と `scripts/dev.ps1`(Windows PowerShell 用)を
同一ターゲット名で併設。ローカル検証は dev.ps1 を正とする(make が未導入のため)。

## D-006: 実 API はキー未設定でも全機能デモ可能(2026-07-04)

Anthropic/YouTube/TTS の実クライアントは実装するが、API キー・OAuth 未設定の環境では
Fake プロバイダーで全ワークフローが動作する。プロバイダー選択は設定
(`LLM_PROVIDER=fake|anthropic` 等)。テストは常に Fake。

## D-007: FFmpeg は winget 導入 + フルパス解決(2026-07-04)

winget(Gyan.FFmpeg)で導入。PATH 反映遅延に備え、設定の `FFMPEG_PATH`/`FFPROBE_PATH` が
未指定なら PATH → WindowsApps エイリアス → winget Links の順で解決するロジックを持つ。

## D-008: 動画生成物は `generated/` 配下のみ(2026-07-04)

パス検証はこのディレクトリ配下であることを必須にし、ディレクトリトラバーサルを拒否。
git 管理外。`make clean-generated` で削除可能。

## D-009: ADR-0004〜0007 を承認(2026-07-04, architectレビュー起点)

architect(opus)の設計レビュー指摘(BLOCKER 2件)を受け、ADR-0004(エンティティ生成の
冪等キー)、0005(アップロードreconcile・fail-closed)、0006(状態機械の復旧エッジ/
REJECTED/準終端)、0007(整数マイクロUSD+予算アトミック予約)をすべて承認。

## D-010: VideoProject は (topic_id, generation) 世代管理(2026-07-04)

1 Topic = 1 Project 固定にせず、`(topic_id, generation)` UNIQUE を最初から導入。
MVPでは generation=1 のみ使用。作り直し要件が出ても スキーマ変更不要。

## D-011: 予算予約はMVPスコープに含める(2026-07-04)

check-then-act 競合は eager 単一ワーカーでは顕在化しないが、BudgetLedger への
条件付きUPDATE(SQLite/PG両対応)で実装コストが低いため、MVPで reserve→commit/release を実装。

## D-012: reconcile はFakeで完全実装、実YouTubeは同一コードパス(2026-07-04)

ADR-0005 の reconcile(idempotencyマーカー突合)は Fake YouTube に list API を持たせて
E2Eで検証する。実プロバイダーも同じインターフェースを実装(実アカウント検証はMVP外)。

## D-013: DB は同期 SQLAlchemy、プロバイダーは async(2026-07-04)

Celery タスク(同期)と FastAPI の両方から同じ repository/service を使うため、DB アクセスは
同期 SQLAlchemy 2.0(psycopg 3 / sqlite3)。LLM/TTS/YouTube プロバイダーは仕様§9どおり
async Protocol とし、Celery タスク内では asyncio.run() で呼ぶ。FastAPI の DB 依存
エンドポイントは def(スレッドプール実行)にする。
