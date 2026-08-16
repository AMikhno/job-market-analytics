# CLAUDE.md

Context and conventions for AI agents (and humans) working on this repo.

## What this project is

Automated job-matching pipeline. **V1 (current) is ingestion + dbt transformations
only** — no LLM, no embeddings, no scoring. Python pulls postings from every ATS with
a public, keyless feed (Greenhouse, Lever, Ashby, BambooHR, Recruitee, Workable, Pinpoint,
Rippling and SmartRecruiters today — ADR-0013/0021; Workday and the auth-gated ATS stay
inventory-only) into per-source raw tables; one dbt project transforms them through
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
- **The company list is private config** (gitignored locally): the master is
  `config/companies.csv`, committed only as `config/companies.example.csv`. It targets public job
  boards, so in CI it is a GitHub Actions **variable** (`COMPANIES_CSV_CONTENT`), *not* a secret —
  only credentials (BigQuery, SMTP) are secrets. One row per company per board; put companies on
  unsupported ATS in with `active=false`. Never commit the real list.
  - **The master and the CI projection are different files.** The pipeline reads only
    `active=true`, so `make companies-variable` writes `config/companies.active.csv` (same columns,
    active rows only) and *that* goes into the variable — a variable caps at 48 KB and the
    inventory grows fastest. Never paste the master in.
  - **`website` is a column** and is not pipeline input: it is the recovery key that lets discovery
    re-derive a `board_ref` after a company moves ATS. Don't drop it to save space.
  - **Restaging merges, never overwrites** (`make update-company-list` → `ingest/merge_companies.py`):
    the master wins on every field, blanks get filled, and an ATS move is reported as a conflict
    for a human. Hand-corrected refs (a corporate suffix the company name alone never
    yields) must survive a refresh.
  - Run `make validate-companies` before pushing the variable — it format-checks every `board_ref`
    so a bad row fails locally, not mid-run. Note it validates **before** any fetch, so a malformed
    ref fails the *entire* run; a merely wrong-but-well-formed ref 404s and skips one board.
  - **Ashby refs may contain single inner spaces** (`Two Words`) — its board names are
    display names. Greenhouse/Lever stay strict bare tokens.
- **Discovery output belongs in `config/discovery/`**, never `~/Downloads`: the audit cache is the
  only record of every company's website and detected ATS, and it is what makes a re-run resumable.
  Only the input candidate `.xlsx` comes from outside the repo (`make discover XLSX=…`).
- **Filter rules are data**: allowed locations, deal-breaker tech, and the desired-tech /
  desired-title signals all live in dbt seeds. **`allowed_locations` is the only one that
  removes a posting** — the tech/title seeds (positive and negative alike) rank it
  (ADR-0015, ADR-0023).
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
- Agents do not run `gh` and do not work in the main branch. Agents can commit and push in feature branches.

## What documentation is for

This repo is public, and it is a **portfolio artifact, not a work journal**. Everything tracked
describes **the system**: what it does, what it costs, and which engineering tradeoff produced its
current shape.

**Explain a decision when the reason is technical** and checkable by a stranger:

- Ashby was built because it is shaped like Lever — one keyless GET, bare-token ref, so the
  adapter is a copy rather than a new access method.
- Workday is deferred because it is not: POST body, offset pagination, and a multi-segment ref
  (tenant/shard/site) instead of a slug.
- V2 scores requirements text because a title names the org chart the seat sits in, while the
  requirements name the daily work.

**Do not explain a decision whose reason is personal.** Record the decision and its ordering — that
is sufficient. Do not substitute a euphemism, and **do not point at a private file**: writing "see
the private note" discloses that a personal reason exists, which is the thing being withheld.

Personal context — employer history, work eligibility, seniority, compensation, preferences,
availability, confidence — is **input to decisions, never content**. It lives in
`config/resume.yaml`, `NOTES.local.md`, or agent memory. The resume corpus is the densest
concentration of it in the tree — gitignored, and a CI **secret** rather than a variable
(ADR-0027).

> **Test:** would this sentence make sense to a stranger reading the repo as an engineering
> artifact? If it only reads as sensible because you know the author's situation, delete it.

Three hard prohibitions on top of that, none of which have an exception:

- **No company names or `board_ref` values** in any tracked file — comments, tests, docs and
  commit messages included. The company list is private precisely because it identifies the
  search; a name in a docstring leaks the same thing the gitignore protects. Use the property
  being illustrated instead ("a two-word Ashby ref", not the ref).
- **No real PII, and no unredacted captured API responses.** Fixtures are sanitized before commit.
- **No numbers that nothing verifies.** A count stated in prose rots the moment code moves under
  it, so assert it: `9 sources <!-- check:sources -->`, enforced by `make docs-check`.

Write once, link elsewhere. One fact has one home — the same fact restated in four files is why
docs drift, and it is how a superseded claim survived four days in `docs/research/ats-feeds.md`.

## What NOT to do

- Don't add an LLM, embeddings, or scoring to V1 — that's V2 by design.
- Only add sources with a public, keyless feed in V1; anything needing auth or scraping
  stays inventory-only (`active=false`) — see ADR-0013.
- Don't create per-source Python files for sources that fit an existing adapter.
- Don't introduce YAML source configs. Pydantic only.
- Don't read `.env` directly; use `shared/config.py`.
- Don't install into the system/user Python. Every Python command goes through `uv run`
  (or a `make` target, which already wraps it); dependencies change only via `uv add`,
  which must land `pyproject.toml` **and** `uv.lock` in the same commit. Never `pip install`.
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
- [ ] **No company name, `board_ref`, or personal context in anything tracked** — including the
      commit message. `make docs-check` passes.
- [ ] Change matches the scope discussed.

## Commands

`make install · ingest · validate-companies · discover · update-company-list · companies-variable · deliver · dbt-dev · dbt-prod · dbt-test · freshness · test · lint · format · check · whois`

## Pointers

**Start every session with [TODO.md's priority list](TODO.md#priority).** It carries what is next,
in order, and what is deliberately *not* being worked on.

When one doc points at part of another, **link to a heading anchor** — `](TODO.md#priority)` — never
a section number or a quoted section title. Anchors are verified by `make docs-check`; prose
references like "§5.6" are unverifiable by construction and were wrong within one edit.

- Current state, priorities, open decisions → `TODO.md`
- How the system works today → `ARCHITECTURE.md` (no roadmap and no history: those are `TODO.md`
  and git respectively)
- Decision records (why, not what) → `docs/decisions/` — newest first: 0025 V2 model + its
  global-only endpoint (supersedes 0009), 0024 one match_score orders the digest,
  0023 deal-breakers demote rather than delete, 0020 V2 scope
- Measured evidence & proposals not yet decided → `docs/research/`
  (`relevance-signals.md` — what actually predicts relevance, and the four instructions V2's
  prompt has to encode; `ingestion-cost.md` — cost model + proposal awaiting evaluation;
  `ats-feeds.md` — per-ATS probe results incl. an "as built" section;
  `workday-ref-discovery.md` — supersedes that file's Workday row;
  `careers-page-tail.md` — the 334 no-ATS companies, and why extraction is the wrong tool;
  `openjobdata.md` — the aggregated-source gate)
- One-off probe scripts behind those numbers → `tools/probes/` (not pipeline code, no tests)
- V2 implementation contract → `docs/v2-plan.md`

Numbers in these docs are **measured, not estimated** — if you supersede one, measure again and
say when. Don't trust a figure's age.
