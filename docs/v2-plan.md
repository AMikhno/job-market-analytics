# V2 implementation plan — AI relevance inside dbt

The contract for the V2 build. Scope fixed by ADR-0020; design rationale in
[ARCHITECTURE.md V2](../ARCHITECTURE.md#v2) and ADR-0003/0004/0025/0027. This document is the
work breakdown an implementation session executes top-to-bottom — decisions
here are settled; re-derive nothing, but **verify current BigQuery AI-function
names/signatures before writing SQL** (they churn). Model choice and its region
constraint are measured in ADR-0025; re-measure rather than trusting their age.

## Scope

**In:** typed extraction (`int_jobs_structured`), fit scoring (`int_jobs_scored`),
resume corpus + prompt rendering, score-aware gold + digest, dev-target stubs,
tests, docs/ADR sweep.

**Out (parked):** embeddings, new ATS adapters (ADR-0013), openjobdata (ADR-0017), and score
thresholds or delivery filtering — the last two both reserved by ADR-0020.

## Settled: where inference runs

The region gate is closed (ADR-0026). The warehouse moved to `us-central1` because
`northamerica-northeast2` served neither a foundational model nor any embedding model, and
BigQuery enforces co-location between a query and the datasets it reads.

**`AI.SCORE` needs no connection.** Its signature makes `connection_id` and `endpoint` optional —
measured, by calling it — so there is no resource to provision and no service account to grant.
Prefer it over `AI.GENERATE_INT`, which does need a connection, unless the 1–5 contract or the
provenance columns cannot be expressed through it.

## Human preconditions (before the prod run; the build itself needs none of these)

- [ ] Vertex AI API enabled.
- [ ] Application Default Credentials for local prod runs
      (`gcloud auth application-default login`) — CI uses Workload Identity Federation and needs
      none.
- [ ] `config/resume.yaml` filled from the example (private, gitignored — never committed).
- [ ] In CI/prod, resume content is a GitHub Actions **encrypted secret**
      `RESUME_YAML_CONTENT`, materialized to `config/resume.yaml` at run start.
      **Not a variable**, unlike the company list: variables are unencrypted, this repo is
      public, and the corpus carries employer history, work authorization and education
      (ADR-0027). The company list is a variable because it names public job boards; a resume
      is real PII and belongs with the credentials under ADR-0007's boundary. It is well
      inside the 48 KB cap.

## Work items (each = one conventional commit with tests, per CLAUDE.md)

### 1. Resume corpus (`shared/resume.py`, `config/resume.example.yaml`) — **built**
- Pydantic model per ADR-0027: `summary`, `seniority`, `constraints`, grouped `skills`,
  `work_history` (roles → bullets), `projects`, `education`. No `target_roles`.
- `evidence_units(resume)`: every bullet as a standalone embeddable unit carrying its
  origin — the granularity resume-to-requirements matching needs.
- `render_prompt(resume) -> str`: deterministic, versioned prompt block
  (`PROMPT_VERSION` constant lives here; bump it whenever wording changes —
  it is provenance in the scored table).
- Loader mirrors `companies.csv` fallback: real file if present, else example
  (with a warning). Tests: schema validation, deterministic rendering, fallback.

### 2. `int_jobs_structured` (silver, incremental, prod-only)
- `AI.GENERATE` (or current equivalent — verify) with `output_schema` emitting typed fields:
  `seniority`, `years_experience_min`, `required_techs` (array), `location_eligibility`,
  and `requirement_text` (requirements/industry only — the chatty portion is dropped).
- Input: `silver_jobs` survivors. Incremental key: `content_hash` with the
  `where content_hash not in (select content_hash from {{ this }})` guard —
  **this guard is a cost control, not just a speed one; never remove it.**
- Injection defense ([ARCHITECTURE V2](../ARCHITECTURE.md#v2)): posting text wrapped in explicit
  delimiters framed as data-not-instructions.
- Failure semantics: null/failed generations land with `extract_ok = false`,
  retried next run (guard on `content_hash` + `extract_ok`), never silently
  dropped or scored.
- Schema evolution: `on_schema_change: append_new_columns` on both incremental
  models; a `--full-refresh` re-bills the entire backfill (~200s per model at 1,742
  texts; cost measured in ADR-0025) and must be a deliberate decision, not a reflex
  ([rebuilds](../ARCHITECTURE.md#rebuilds-not-migrations), "Schema evolution").
- **Dev parity:** on the DuckDB target the model is a stub emitting the same
  columns as typed nulls (`enabled`/target-conditional SQL).
  Downstream models and unit tests run against the stub.

### 3. `int_jobs_scored` (silver, incremental, prod-only)
- Scoring function: **evaluate `AI.SCORE` first** (GA 2026, natively managed — no
  resource connection to provision; rubric-in-prompt, rating output), falling back to
  `AI.GENERATE_INT` (also GA, needs the Vertex connection) if AI.SCORE can't express
  the 1–5 contract or its provenance needs. Whichever is chosen: temperature-0
  semantics, prompt = resume block (static prefix, from `var('resume_prompt')` —
  static so Gemini context caching discounts it) + the trimmed `requirement_text` —
  never the full posting.
- Columns: `fit_score` (1–5), `model`, `prompt_version`, `scored_at`.
- Same incremental guard; re-score is triggered by `content_hash` change or
  `prompt_version` bump.
- Workflow passes the rendered resume: a make target renders
  `config/resume.yaml` → `--vars` (add to `ingest.yml` dbt-prod step).
- Range validation: `accepted_values` on 1–5 (out-of-range = flagged, not delivered).

### 4. Gold + digest become score-aware
- `fct_job_postings` gains `fit_score`, `prompt_version`, `scored_at` (nullable —
  a not-yet-scored posting still ships). Out-of-range scores are nulled here
  (flagged upstream), so delivery never shows a bogus number.
- `deliver/digest.py`: order `fit_score desc nulls last`, then the existing
  soft signals; show the score (e.g. `[fit 4/5]`); unscored postings say so.
  Digest tests extend the existing DuckDB-seeded pattern.

### 5. Docs
- ARCHITECTURE's V2 section → "as built"; TODO sweep; record the measured
  first-backfill cost into ADR-0025 (which leaves it open), and how to sanity-check it
  (row counts in `int_jobs_structured` vs silver survivors after run 1).

## Acceptance

- [ ] Full local suite green: `make check`, `dbt build --target dev` (stubs), prod `dbt parse`.
- [ ] Coverage gate untouched (≥85), no swallowed exceptions, mypy --strict clean.
- [ ] Unit tests: prompt rendering; stub columns; gold null-score passthrough;
      digest ordering with scores; out-of-range score nulled.
- [ ] Schema tests: `accepted_values` 1–5, `not_null` on provenance columns where scored.
- [ ] First prod run: backfill cost sanity-checked; incremental second run processes ~0 rows.
