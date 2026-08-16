# Job Search Pipeline

End-to-end **Analytics Engineering** project: typed Python ingestion from every ATS with a
public keyless feed (nine today — Greenhouse, Lever, Ashby, BambooHR, Recruitee, Workable,
Pinpoint, Rippling, SmartRecruiters) into a **dual-target dbt project** (DuckDB
dev / BigQuery prod) that dedupes, lifecycle-tracks, and rule-filters postings — then emails
a digest of what's new. LLM relevance scoring (in-warehouse, BigQuery AI) is the scoped V2.

```mermaid
flowchart LR
    A[ATS APIs<br/>9 public keyless feeds] -->|"Python adapters<br/>(typed, tested, parallel)"| B[(raw_*_jobs<br/>append-only)]
    B --> C[bronze<br/>stg views]
    C --> D[silver<br/>union · dedup · lifecycle<br/>filters + soft signals]
    D --> E[gold<br/>fct_job_postings<br/>live postings only]
    E -->|watermark| F[email digest<br/>new postings only]
    D -.->|V2| G[LLM extraction<br/>+ fit scoring]
    G -.-> E
```

## Design highlights 

- **Medallion = dbt-native.** Three zones map 1:1 onto staging → intermediate → marts
  (ADR-0010), each in its own BigQuery dataset (ADR-0014); raw is append-only with
  partition expiry, and *all* transforms rebuild from it — so filter-rule changes apply
  retroactively with zero migrations.
- **One SQL codebase, two warehouses.** Every model runs on DuckDB (dev/CI, secretless)
  and BigQuery (prod); dialect differences live in `adapter.dispatch` macros — including
  regex-literal escaping and timestamp arithmetic where naive cross-db macros have
  prod-only type traps.
- **Lifecycle, not snapshots.** `first_seen_at` / `last_seen_at` / `is_active` are derived
  from the append-only landing (the source APIs have no date filters), with a staleness
  rule so boards removed from the list age out instead of leaving zombie postings.
- **Rules are data.** Deal-breaker tech, allowed locations, desired tech/titles are dbt
  seeds — word-boundary matched, regex-safe (`C++`, `.NET` work), unit-tested. Hard rules
  drop; soft signals only annotate and order (ADR-0015) — recall stays high until the LLM
  can judge fit.
- **Security is structural.** No secret values in the repo; BigQuery auth via Workload
  Identity Federation (keyless); GH Actions SHA-pinned (`id-token: write` hygiene);
  gitleaks in CI; the company list and candidate profile are private config, never
  committed.
- **Tests gate every commit.** 110 pytest tests (95%+ coverage, enforced), 40 dbt
  schema/unit tests, mypy `--strict`, sqlfluff, plus a CI parse of the prod target — a
  fork-safe pipeline with no secrets in CI.
- **Every non-obvious choice has an ADR** — 27 so far, in `docs/decisions/`. <!-- check:adrs -->

**Browse the [dbt docs & lineage DAG](https://amikhno.github.io/job-market-analytics/)** —
generated in CI on every push to main (raw sources → bronze → silver → gold → the email-digest
exposure), or locally via `make dbt-docs`.

## Quickstart

Prerequisite: [uv](https://docs.astral.sh/uv/) (the only one — it installs Python 3.14
itself, per `.python-version`).

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS/Linux; or: brew install uv

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
ingest/      per-ATS adapters, source registry, parallel pipeline entrypoint
shared/      config (Pydantic Settings), models, http, storage (one writer, both warehouses)
deliver/     email digest of new postings (watermark in ops.digest_runs)
config/      private company list (gitignored; .example committed)
dbt/         one dual-target project: models/{bronze,silver,gold}, seeds, macros, unit tests
scripts/     repo gates that are not lint or tests (see below)
tests/       pytest suite + sanitized fixtures
docs/        decisions/, v2-plan.md, research/  (ARCHITECTURE.md at repo root)
.github/     ci.yml (DuckDB, secretless, fork-safe) + ingest.yml (scheduled, WIF, SHA-pinned)
```

Currently 9 ingestion sources, 12 dbt models and 27 ADRs. <!-- check:sources check:dbt_models check:adrs -->

## Quality gates

Every invariant this project relies on is enforced by something that fails, not by intent.
`make lint` and `make test` run locally, in a pre-commit/pre-push hook, and again in CI, which
blocks merge:

| Gate | Guards against |
|---|---|
| `ruff check` + `ruff format` | style drift, unused code, import order |
| `mypy --strict` | untyped defs; no `Any` crossing a module boundary |
| `sqlfluff` | dbt SQL style, against the compiled models |
| `pytest` with `--cov-fail-under=85` | untested behavior; the gate is never lowered to pass |
| dbt schema + unit tests | model contracts (`not_null`, `unique`, `accepted_values`) |
| `dbt source freshness` | a source that silently stopped landing rows |
| **`scripts/check_docs.py`** | **documentation that points at things which no longer exist** |
| `gitleaks` | committed secrets |
| Dependabot | stale dependencies (`uv.lock` + `pyproject.toml` together) |

The docs check is the unusual one. Prose drifts silently — a file is renamed, a make target goes
away, an ADR is superseded, and the sentence referring to it still reads fine, because nothing in
a test suite imports a paragraph. It verifies the references that are mechanically checkable
(relative links, repo paths, line-anchored refs, ADR numbers, make targets, dbt model names,
heading anchors) across both markdown and Python docstrings, and lets a doc assert a number
against the code with an inline `<!-- check:… -->` marker, so a count cannot rot under an edit.
Deliberate exceptions live in `scripts/planned-refs.txt`; everything else failing is drift.

## Stack

Python 3.14 + Pydantic v2 · dbt-core with dbt-duckdb (dev) and dbt-bigquery (prod) ·
GitHub Actions (twice-daily ingest, freshness gate, email digest of new postings).

## Status & roadmap

**V1.6 in production** — twice-daily ingestion to BigQuery, transform, freshness gate,
email digest. **V2 (scoped, ADR-0020):** LLM extraction + 1–5 fit scoring inside BigQuery
(`AI.SCORE` / `AI.GENERATE_INT`), score-*ordered* (never filtered) delivery — plan in
`docs/v2-plan.md`. Full design: `ARCHITECTURE.md`.

## License

Personal project. Not currently licensed for redistribution.
