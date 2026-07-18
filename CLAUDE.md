# CLAUDE.md

Context and conventions for AI agents (and humans) working on this repo.

## What this project is

Automated job-matching pipeline. **V1 (current) is ingestion + dbt transformations
only** — no LLM, no embeddings, no scoring. Python pulls postings from every ATS with
a public, keyless feed (Greenhouse, Lever, Ashby today; more ATS are tentative V2 — see
ADR-0013) into per-source raw tables; one dbt project transforms them through
bronze → silver → gold into a deduplicated, rule-filtered table of postings.
AI (LLM structuring/scoring, embeddings) is **V2**. See `ARCHITECTURE.md`.

## Non-negotiable: tests cover every change, and they pass

- **Every commit** — by an agent or a human — must include tests for the change,
  and the full suite must pass. This is enforced, not aspirational:
  - a `pre-push` hook runs `pytest` with a coverage gate (`--cov-fail-under=85`);
  - CI re-runs `make lint` + `make test` + `dbt build`/`dbt test` and **blocks merge** on failure.
- New Python behavior → a `pytest` test (adapters/scrapers test against a committed,
  sanitized fixture in `tests/fixtures/`). New dbt model/column → a schema test
  (`not_null`, `unique`, `accepted_values`, `relationships`) in the model's `.yml`.
- Do not lower the coverage gate or delete tests to make a change pass.

## Conventions

- **Python 3.14+**, full type hints; `mypy --strict` must pass. No untyped defs.
- **Pydantic v2** for all configs and data models. No raw dicts crossing module
  boundaries (the one entry point — API JSON — is parsed into a `RawPosting` in the adapter).
- **One adapter per access method, one scraper per genuinely unique site.**
- **Source definitions** live in `ingest/sources.py` (Pydantic registry), NOT YAML.
- **The company list is private config** (gitignored locally): real list in `config/companies.csv`,
  committed only as `config/companies.example.csv`. It targets public job boards, so in CI it is a
  GitHub Actions **variable** (`COMPANIES_CSV_CONTENT`), *not* a secret — only credentials (BigQuery,
  Slack) are secrets. One row per company per board; put companies on unsupported ATS in with
  `active=false`. Never commit the real list. Run `make validate-companies` before pasting a list
  into the variable — it format-checks every `board_ref` so a bad row fails locally, not mid-run.
- **Filter rules are data**: deal-breaker tech, allowed locations, and the soft desired-tech /
  desired-title signals all live in dbt seeds.
- **Cross-warehouse SQL**: models must run on both DuckDB (dev) and BigQuery (prod);
  dialect-specific logic goes in an `adapter.dispatch` macro (see `macros/cross_db.sql`).
- **Conventional commits** (`feat:`, `fix:`, `chore:`, `docs:`); small, single-purpose.

## Secrets — a hard boundary, not a polite request

The repo and anything an agent can read must never contain a secret *value*.

- **V1 ingestion sources need no credentials** — the supported ATS read APIs (Greenhouse,
  Lever, Ashby) are public and keyless.
- Secret **values** live only in (a) GitHub Actions Encrypted Secrets and (b) your OS
  keychain / gcloud ADC. Neither is a file in the working tree.
- The repo contains only secret **names** (in workflow YAML) and **placeholders** (`.env.example`).
- BigQuery auth uses **Workload Identity Federation** — there is no service-account key file.
- `gitleaks` runs as a pre-commit hook. `.env`, `*.duckdb`, and key files are gitignored.
- Agents do not run `git push` or `gh`; a human authenticates and pushes.

## Public-repo / PII

This is a portfolio (public) repo. Do **not** commit: real candidate PII, unredacted
captured API responses, or `config/profile.yaml` with real personal data. Test fixtures
must be sanitized before they are committed.

## What NOT to do

- Don't add an LLM, embeddings, or scoring to V1 — that's V2 by design.
- Only add sources with a public, keyless feed in V1; anything needing auth or scraping
  stays inventory-only (`active=false`) — see ADR-0013.
- Don't create per-source Python files for sources that fit an existing adapter.
- Don't introduce YAML source configs. Pydantic only.
- Don't read `.env` directly; use `shared/config.py`.
- Don't swallow exceptions. The default is log + re-raise; a deliberate catch is allowed
  only when it (a) has a one-line comment stating why and (b) still surfaces failure via
  the run's exit status (see `ingest/pipeline.py`, which records failures and exits non-zero).
- Don't add dependencies without asking first.

## Workflow expectations

- **Plan before large changes**: if a task would touch more than 3 files OR add more than
  ~100 net lines (production code, excluding generated files), outline the plan and wait
  for confirmation. Splitting one logical change across several small commits to dodge
  this threshold defeats its purpose — don't.
- A "non-trivial function" (which requires a test) is anything with branching, I/O, parsing,
  or a return value other than a trivial passthrough. When unsure, write the test.
- **When uncertain, ask.**

## Self-review checklist (verify, don't assume)

- [ ] Tests added for the change and the full suite passes (`make test`).
- [ ] Coverage gate still met; gate not lowered.
- [ ] dbt schema tests added for new models/columns; `make dbt-test` passes.
- [ ] `make lint` passes (ruff check, ruff format, mypy --strict, sqlfluff).
- [ ] No swallowed exceptions; no TODO/placeholder values left behind.
- [ ] No secret values, no real PII, no unredacted fixtures.
- [ ] Change matches the scope discussed.

## Commands

`make install · ingest · validate-companies · dbt-dev · dbt-prod · dbt-test · freshness · test · lint · format · check`

## Pointers

- System design & roadmap → `ARCHITECTURE.md`
- Decision records → `docs/decisions/`
