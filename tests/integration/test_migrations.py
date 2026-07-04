"""PostgreSQLに対するAlembicマイグレーションの統合テスト。

DATABASE_URL がPostgreSQLを指す場合のみ実行する。docker compose / CIの
サービスコンテナで用意されたPostgreSQLに対して検証するためのテストであり、
それ以外の環境ではDBが存在しないため意図的にskipする。
"""

from __future__ import annotations

import os
import subprocess  # noqa: S404 引数配列のみで使用。shell=Trueは使わない。
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_alembic_upgrade_head_against_postgres() -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith("postgresql"):
        pytest.skip("DATABASE_URL does not point to PostgreSQL; skipping integration test")

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
