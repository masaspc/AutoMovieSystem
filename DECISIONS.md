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
