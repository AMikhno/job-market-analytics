# Job Search Pipeline

Automated pipeline that ingests job postings, deduplicates and rule-filters them, and
(in V2) ranks them against a personal profile using an LLM. **V1 is ingestion + dbt
transformations only**, against **every ATS with a public, keyless feed** (Greenhouse,
Lever, and Ashby today; more ATS are tentative V2).

## Why

- **Practical** — surface relevant Analytics Engineer / BI / Data roles in Ottawa or
  Canada-remote quickly, filtering out Data-Engineer-flavored roles (Kafka/Spark/etc.).
- **Portfolio** — an end-to-end Analytics Engineering project: typed Python ingestion,
  a dual-target dbt project (DuckDB dev / BigQuery prod), tests + CI, and a clear V2 path
  to LLM-in-warehouse scoring and embeddings.

## Quickstart

```bash
make install                  # uv venv + pre-commit hooks
cp .env.example .env          # ingestion needs no secrets; fill BQ vars only for prod
cp config/companies.example.csv config/companies.csv   # your PRIVATE company list (gitignored)
# dbt/profiles.yml is committed (env-var driven, no secrets) — nothing to copy

make ingest                   # Python -> raw tables (DuckDB by default)
make dbt-dev                  # bronze -> silver -> gold on DuckDB
make test && make dbt-test
```

## Structure

```
ingest/      per-ATS adapters (Greenhouse, Lever, Ashby), source registry, pipeline entrypoint
shared/      config (Pydantic Settings), models, http, storage
config/      private company list (config/companies.csv, gitignored; .example committed)
dbt/         one dual-target project: models/{bronze,silver,gold}, seeds, macros
tests/       pytest suite + sanitized fixtures
docs/        decisions/ (ADRs), build-plan.md, fix-roadmap.md  (ARCHITECTURE.md is at repo root)
.github/     ci.yml (DuckDB, no secrets) + ingest.yml (scheduled, WIF)
```

## Stack

Python 3.14 + Pydantic v2 · dbt-core with dbt-duckdb (dev) and dbt-bigquery (prod) ·
GitHub Actions (twice-daily ingest, freshness gate, Slack-on-failure).

## Status

V1 built — ingestion + dbt (bronze → silver → gold), tests + CI. AI scoring/embeddings
are V2; see `ARCHITECTURE.md` §9 for the roadmap.

## License

Personal project. Not currently licensed for redistribution.
