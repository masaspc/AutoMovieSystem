# ADR-0002: PostgreSQL本番 + SQLite互換モデル

Status: Accepted (2026-07-04)

## Context
本番・composeはPostgreSQL 16。ただし開発機(Windows)はDocker導入直後で常時起動が
保証されず、単体/E2Eテストの高速性とDocker非依存の検証手段が必要。

## Decision
- SQLAlchemyモデルはSQLiteでも動作する型のみ使用(JSON, String UUID, DateTime)。
  JSONB/ARRAY/サーバーサイドUUID等のPG専用機能を使わない
- unit/e2e/contract テストはSQLite in-memory + Celery eager
- integration テストのみPG/Redis必須(`-m integration`、CIサービスコンテナで常時実行)

## Consequences
- PG固有の性能最適化(JSONBインデックス等)はMVP後の課題
- マイグレーションはPGで検証し、テスト用スキーマは metadata.create_all を許可(e2e/unit)
