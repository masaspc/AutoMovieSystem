.PHONY: setup up down migrate seed lint typecheck test test-unit test-integration test-e2e demo security-check clean-generated

setup:
	uv sync

up:
	docker compose up -d --build

down:
	docker compose down

migrate:
	uv run alembic upgrade head

seed:
	uv run python scripts/seed.py

lint:
	uv run ruff check .

typecheck:
	uv run mypy app

test:
	uv run pytest -q

test-unit:
	uv run pytest tests/unit -q

test-integration:
	uv run pytest tests/integration -q -m integration

test-e2e:
	uv run pytest tests/e2e -q -m e2e

demo:
	uv run python scripts/demo.py

security-check:
	uv run pip check
	uv run pip-audit

clean-generated:
	uv run python -c "import shutil; from pathlib import Path; p = Path('generated'); shutil.rmtree(p, ignore_errors=True); p.mkdir(exist_ok=True)"
