from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_editorial_policy_migration_upgrade_downgrade_roundtrip(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    database_path = tmp_path / "migration.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))

    command.upgrade(config, "c47a2f9b1d3e")
    engine = create_engine(database_url)
    assert "editorial_policy" not in {
        column["name"] for column in inspect(engine).get_columns("channels")
    }

    command.upgrade(config, "head")
    assert "editorial_policy" in {
        column["name"] for column in inspect(engine).get_columns("channels")
    }

    command.downgrade(config, "-1")
    assert "editorial_policy" not in {
        column["name"] for column in inspect(engine).get_columns("channels")
    }

    command.upgrade(config, "head")
    assert "editorial_policy" in {
        column["name"] for column in inspect(engine).get_columns("channels")
    }
