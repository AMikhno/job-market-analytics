.PHONY: install ingest validate-companies dbt-deps ensure-raw dbt-dev dbt-prod dbt-test freshness test lint sql-lint format check

install:          ## Set up the uv venv, dbt packages, and pre-commit hooks
	uv sync --extra dev
	cd dbt && uv run dbt deps
	uv run pre-commit install --install-hooks
	uv run pre-commit install --hook-type pre-push

ingest:           ## Run the ingestion pipeline once (Python -> raw tables)
	uv run python -m ingest.pipeline

validate-companies: ## Pre-flight check the company list (board_ref formats) before use
	uv run python -m ingest.validate_companies

dbt-deps:         ## Install dbt package dependencies (dbt_utils)
	cd dbt && uv run dbt deps

ensure-raw:       ## Create empty raw tables so dbt can build without an ingest run
	uv run python -c "from shared.config import get_settings; from shared import storage; storage.ensure_raw_tables(get_settings())"

dbt-dev: dbt-deps  ## Build the dbt DAG against local DuckDB
	cd dbt && uv run dbt build --target dev

dbt-prod: dbt-deps ## Build the dbt DAG against BigQuery
	cd dbt && uv run dbt build --target prod

dbt-test:         ## Run dbt tests
	cd dbt && uv run dbt test --target dev

freshness:        ## Assert raw sources are fresh (fails the run if stale/empty)
	cd dbt && uv run dbt source freshness --target prod

test:             ## Run the Python test suite with coverage gate
	uv run pytest

lint: sql-lint    ## ruff (check + format) + mypy + sqlfluff
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy shared ingest

sql-lint: dbt-deps ## Lint dbt SQL (dbt templater, DuckDB dialect; run from dbt/)
	mkdir -p data                         # DuckDB path the profile resolves to
	cd dbt && uv run sqlfluff lint models

format:           ## Auto-fix with ruff (lint + format) and sqlfluff
	uv run ruff check --fix .
	uv run ruff format .
	cd dbt && uv run sqlfluff fix models

check: lint test  ## Everything CI runs locally
