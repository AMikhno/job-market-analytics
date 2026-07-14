# Fix Roadmap

Findings from the 2026-07-06 full-repo audit (code + docs + live API check), ordered as a
work plan. Each item is a checkbox; check it off in the PR that fixes it. Numbers are
stable — refer to items as FR-1 … FR-18.

Suggested order: **Phase 1** (data correctness), **Phase 2** (prod path), **Phase 3**
(daily-use gaps), **Phase 4** (dbt modernization), **Phase 5** (doc sweep, one commit).

## Status (updated 2026-07-14)

**Done — FR-1 … FR-12** (Phases 1–3 + dbt unit tests), plus a whitespace-strip fix
for the company-list CSV found while verifying against the real list. Verified
end-to-end: real ingest (507 rows), `dbt build` 35/35 (incl. 5 unit tests), source
freshness PASS on both sources, lineage resolves to `fct_job_postings`.

**Remaining — FR-13, FR-14, FR-15 (partial), FR-16, FR-17, FR-18** (contracts,
sqlfluff, remaining dbt polish, the doc sweep, company-list secret, toolchain
alignment). FR-15's `recency_rank` and the redundant CI `dbt seed` are already done;
column descriptions + `persist_docs` remain.

---

## Phase 1 — Data correctness (already ingesting wrong)

- [x] **FR-1. Greenhouse `content` is HTML-escaped; adapter assumes raw HTML.**
  Verified against the live API: `content` arrives as `&lt;p&gt;…`, not `<p>…`.
  `strip_html` therefore strips nothing for Greenhouse, `clean_text` is entity soup, and
  V2 prompts would inherit it. Fix: `html.unescape()` in
  `ingest/adapters/greenhouse.py::_map`, and regenerate
  `tests/fixtures/greenhouse_jobs.json` from a real (sanitized) response so the fixture
  matches the actual API shape.

- [x] **FR-2. Dedup order breaks for edited Lever postings.**
  `silver_jobs.sql` dedups by `posted_or_updated_at desc`, but Lever's timestamp is
  `createdAt` (never changes) → every re-ingest ties and the surviving row is arbitrary;
  an edited posting may never surface. Fix: order by `ingested_at desc` (append-only
  landing makes "most recently ingested" the correct current row).

## Phase 2 — Prod (BigQuery) path: first scheduled run currently fails twice

- [x] **FR-3. `cast(null as varchar)` in `stg_greenhouse__jobs.sql` errors on BigQuery**
  (no `VARCHAR` type). The cast is also unnecessary — the raw table has a
  `remote_policy` column; select it like `stg_lever__jobs` does.

- [x] **FR-4. `ingest.yml` provisions no dbt profile** — `make dbt-prod` / `make freshness`
  fail with "Could not find profile". Fix: commit the env-var-driven profile as
  `dbt/profiles.yml` (it contains no secrets; dbt finds it in the project dir), drop the
  write-a-profile step from `ci.yml`, and remove `profiles.yml` from `.gitignore`.

- [x] **FR-5. CI never exercises the BigQuery dialect**, so prod-only SQL errors (FR-3)
  surface first in the scheduled run. Add at least a `dbt compile --target prod` CI job
  (env vars faked), and/or sqlfluff with the BigQuery dialect (see FR-13).

- [x] **FR-6. Defensive `where true` above `qualify` in `silver_jobs.sql`** — BigQuery's
  parser has historically rejected `QUALIFY` without a WHERE/GROUP BY/HAVING clause;
  behavior is inconsistent across versions. Costs nothing; verify on first prod run.

## Phase 3 — Architectural gaps for daily use

- [x] **FR-7. Closed postings never leave gold.** Raw is append-only and silver keeps
  "latest per `job_key`", so taken-down jobs sit in `fct_job_postings` forever. Add
  `last_seen_at = max(ingested_at) per job_key` and
  `is_active = (last_seen_at = latest successful ingest for that company)`; gold filters
  or flags. Document the removal story in ARCHITECTURE.

- [x] **FR-8. Company identifier must be a path fragment, not a bare slug.**
  `companies.csv` stores `company_slug` and each adapter formats a single-token URL
  template. That works for Greenhouse/Lever but not for ATS like **Workday**, whose
  boards need multiple parameters (tenant, instance, site — e.g.
  `{tenant}.wd5.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs`). Fix: replace
  `company_slug` with a `board_ref` (or full careers-URL) column holding the part of the
  path the adapter needs — a bare slug for Greenhouse/Lever, a multi-segment path for
  Workday-likes — and have each adapter parse what it needs from it. Update
  ARCHITECTURE §4 ("the slug is the last path segment") and
  `config/companies.example.csv` accordingly. Prerequisite for "more sources" in V2.

- [x] **FR-9. Location gate passes US-remote jobs.** Seed pattern `Remote` substring-matches
  "Remote — US only", and `location IS NULL` passes unconditionally while Greenhouse
  `remote_policy` is always null. Tighten the seed (e.g. `Remote - Canada` variants, drop
  bare `Remote`) or consciously accept the noise and document it.

- [x] **FR-10. BigQuery side is hand-provisioned and streaming-priced.** Datasets/tables
  are created manually ("during cloud setup") with no committed DDL; `insert_rows_json`
  is a paid streaming insert not counted in the §5.5 cost analysis; the documented
  partition-expiry mitigation is implemented nowhere. Fix: make `ensure_raw_tables`
  work in prod too (create dataset + partitioned table with expiry, idempotent) and
  switch to free batch `load_table_from_json`.

- [x] **FR-11. `ingest/sources.py` half dead.** Per-source `url_template` is never used
  (adapters hardcode their own), `tier` is unused, and the docstring still says slugs
  come from `dbt/seeds/companies.csv`. Single source of truth for URLs (fold into FR-8),
  delete dead fields, fix the docstring.

## Phase 4 — dbt modernization (portfolio leverage)

- [x] **FR-12. Add dbt unit tests (dbt-core 1.8+ `unit_tests:`).** The V1 core logic —
  dedup, word-boundary tech matching, location gate — has no logic tests, only
  `not_null`/`unique`. Mock rows should pin: "Kafka a plus" matches, "Kafkaesque"
  doesn't, "Remote — US only" behavior, null location, tie-breaking (FR-2).

- [ ] **FR-13. Enforce model contracts** (`contract: {enforced: true}` + `data_type`) on
  `silver_jobs` and `fct_job_postings`. Would have caught FR-3 at parse time; also
  de-risks the position-based `union all` in `int_jobs__unioned`.

- [ ] **FR-14. Implement sqlfluff or stop claiming it.** CLAUDE.md's checklist and the CI
  step name say lint includes sqlfluff; the Makefile never runs it and there is no
  `.sqlfluff`. Implementing it with the dbt templater + BigQuery dialect also helps FR-5.

- [ ] **FR-15. Small dbt cleanups.** Drop the no-op trailing `order by` in
  `fct_job_postings.sql` (table materializations don't store order) or add a
  `recency_rank` column; add column descriptions + `persist_docs`; drop the redundant
  `dbt seed` step in `ci.yml` (`dbt build` seeds).

## Phase 5 — Docs & config sweep (one commit)

- [ ] **FR-16. Doc/impl contradictions.**
  - ARCHITECTURE §4 still says the company list is `dbt/seeds/companies.csv` "read by
    both Python and dbt" — contradicts ADR-0011, CLAUDE.md, and the code
    (`config/companies.csv`, Python-only, gitignored).
  - `ARCHITECTURE.md` exists twice (root + `docs/`); keep one canonical copy.
  - README points to "§6 for the roadmap" (it's §9).
  - `build-plan.md`: V1 boxes all unchecked though built; "zero-rows-is-failure"
    contradicts the implemented warn-only design.
  - `ci.yml` comment "uses the committed example company list" is wrong (dbt never reads
    it). Docs say `ops.ingest_runs`; reality is `jobs_ops.ingest_runs` (BQ) /
    `ops_ingest_runs` (DuckDB) — pick one notation.

- [ ] **FR-17. Company list secrecy + naming.** CLAUDE.md says treat the list like `.env`,
  but `ingest.yml` reads it from a plaintext GitHub Actions **variable**. Move to an
  encrypted secret, and rename to avoid colliding with the `COMPANIES_CSV` env var that
  holds a *path* (e.g. `COMPANIES_CSV_CONTENT`).

- [ ] **FR-18. Toolchain version alignment.** CLAUDE.md says "Python 3.12+" vs
  `requires-python >= 3.14`; black/ruff `target-version = py312` vs mypy 3.14. Pre-commit
  runs both `ruff-format` and black (pick one formatter) and pins ruff v0.5.0 while the
  venv floats `ruff>=0.5` — align versions. Add `.vscode/` to `.gitignore`.
