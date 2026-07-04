# ADR-0003: Makefile + scripts/dev.ps1 の二重化

Status: Accepted (2026-07-04)

## Context
仕様§24はMakefileターゲットを要求するが、開発機はWindows 11でmake未導入。
CI(GitHub Actions/Linux)と第三者のLinux/Mac環境ではmakeが自然。

## Decision
Makefileを仕様どおり提供しつつ、同一ターゲット名の `scripts/dev.ps1 <target>` を併設。
両者とも実体は `uv run ...` / `docker compose ...` の薄いラッパーとし、ロジックは
Pythonスクリプト(`scripts/*.py`)側へ寄せて重複を防ぐ。

## Consequences
- Windowsローカル検証は dev.ps1 を正、CI・ドキュメントは make を正とする
- ターゲット追加時は両方を更新する(docs-maintainerの照合対象)
