.PHONY: install ingest validate-companies discover update-company-list companies-variable deliver dbt-deps ensure-raw dbt-dev dbt-prod dbt-test dbt-docs freshness test lint sql-lint format check

install:          ## Set up the uv venv, dbt packages, and pre-commit hooks
	uv sync --extra dev
	cd dbt && uv run dbt deps
	uv run pre-commit install --install-hooks
	uv run pre-commit install --hook-type pre-push

ingest:           ## Run the ingestion pipeline once (Python -> raw tables)
	uv run python -m ingest.pipeline

validate-companies: ## Pre-flight check the company list (board_ref formats) before use
	uv run python -m ingest.validate_companies

whois:            ## Resolve a redacted CI-log ref to a company (local only). Usage: make whois REF=redacted:ad589ceb
	@test -n "$(REF)" || { echo "set REF=redacted:xxxxxxxx"; exit 1; }
	@uv run python -m ingest.whois $(REF)

discover:         ## Audit new candidates into config/discovery/ (resumes; only the input comes from outside). Usage: make discover XLSX=~/Downloads/new_candidates.xlsx
	@test -n "$(XLSX)" || { echo "set XLSX=/path/to/candidates.xlsx (Company Name / Website columns)"; exit 1; }
	uv run --with playwright --with openpyxl --with requests --no-project \
	  python tools/company_discovery/ats_audit.py --xlsx "$(XLSX)" $(DISCOVER_ARGS)
	@echo "cache + inventory written to config/discovery/"
	@echo "stage it with: make update-company-list"

update-company-list: ## Merge config/discovery/companies_inventory.csv into the master + validate + project. Override with INV=/path/to/file.csv
	$(eval INV ?= config/discovery/companies_inventory.csv)
	@test -f "$(INV)" || { echo "no inventory at $(INV) — run 'make discover XLSX=…' first"; exit 1; }
	@test -f config/companies.csv && cp config/companies.csv config/companies.csv.bak \
	  && echo "previous master backed up to config/companies.csv.bak" || true
	uv run python -m ingest.merge_companies config/companies.csv $(INV)
	$(MAKE) validate-companies
	$(MAKE) companies-variable

companies-variable: ## Write the active-only projection CI needs + print the push command
	uv run python -m ingest.export_companies > config/companies.active.csv
	@echo "push it (human-authenticated):"
	@echo "  gh variable set COMPANIES_CSV_CONTENT < config/companies.active.csv"

deliver:          ## Email the digest of new gold postings (no-op without SMTP creds)
	uv run python -m deliver.digest

dbt-deps:         ## Install dbt package dependencies (dbt_utils)
	cd dbt && uv run dbt deps

ensure-raw:       ## Create empty raw tables so dbt can build without an ingest run
	uv run python -c "from ingest.pipeline import ensure_raw_tables; ensure_raw_tables()"

dbt-dev: dbt-deps  ## Build the dbt DAG against local DuckDB
	cd dbt && uv run dbt build --target dev

dbt-prod: dbt-deps ## Build the dbt DAG against BigQuery
	cd dbt && uv run dbt build --target prod

dbt-test:         ## Run dbt tests
	cd dbt && uv run dbt test --target dev

dbt-docs: dbt-deps ## Generate self-contained dbt docs (lineage + columns) at dbt/target/index.html
	cd dbt && uv run dbt docs generate --static --target dev

freshness:        ## Assert raw sources are fresh (fails the run if stale/empty)
	cd dbt && uv run dbt source freshness --target prod

test:             ## Run the Python test suite with coverage gate
	uv run pytest

lint: sql-lint docs-check ## ruff (check + format) + mypy + sqlfluff + doc references
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy shared ingest deliver scripts

docs-check:       ## Fail on docs pointing at files/targets/models that don't exist
	uv run python scripts/check_docs.py

sql-lint: dbt-deps ## Lint dbt SQL (dbt templater, DuckDB dialect; run from dbt/)
	mkdir -p data                         # DuckDB path the profile resolves to
	cd dbt && uv run sqlfluff lint models

format:           ## Auto-fix with ruff (lint + format) and sqlfluff
	uv run ruff check --fix .
	uv run ruff format .
	cd dbt && uv run sqlfluff fix models

check: lint test  ## Everything CI runs locally
