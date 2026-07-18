# V2 implementation plan — AI relevance inside dbt

The contract for the V2 build. Scope fixed by ADR-0020; design rationale in
`ARCHITECTURE.md` §5/§5.5/§5.6 and ADR-0003/0004/0009. This document is the
work breakdown an implementation session executes top-to-bottom — decisions
here are settled; re-derive nothing, but **verify current BigQuery AI-function
names/signatures and Gemini model availability before writing SQL** (they churn).

## Scope

**In:** typed extraction (`int_jobs_structured`), fit scoring (`int_jobs_scored`),
profile config + prompt rendering, score-aware gold + digest, dev-target stubs,
tests, docs/ADR sweep.

**Out (parked):** embeddings (ADR-0020 §3), new ATS adapters (ADR-0013),
openjobdata (ADR-0017), score thresholds / delivery filtering (ADR-0020 §2).

## Human preconditions (before the prod run; the build itself needs none of these)

- [ ] BigQuery ↔ Vertex connection `northamerica-northeast2.vertex` created; its service
      account granted Vertex access; Vertex AI API enabled. (§5.6: connection, dataset, and
      endpoint must all be `northamerica-northeast2` — a mismatch is a hard failure.)
- [ ] `config/profile.yaml` filled from the example (private, gitignored — never committed).
- [ ] In CI/prod, profile content follows the company-list pattern (ADR-0011): a GitHub
      Actions **variable** `PROFILE_YAML_CONTENT` materialized to `config/profile.yaml`
      (it contains preferences, not credentials; keep real PII out of it).

## Work items (each = one conventional commit with tests, per CLAUDE.md)

### 1. Profile config (`shared/profile.py`, `config/profile.example.yaml`)
- Pydantic model: `target_roles: list[str]`, `core_skills: list[str]`,
  `nice_to_have_skills: list[str]`, `seniority: str`, `constraints: list[str]`,
  `summary: str` (freeform, few sentences).
- `render_prompt(profile) -> str`: deterministic, versioned prompt block
  (`PROMPT_VERSION` constant lives here; bump it whenever wording changes —
  it is provenance in the scored table).
- Loader mirrors `companies.csv` fallback: real file if present, else example
  (with a warning). Tests: schema validation, deterministic rendering, fallback.
- Add `config/profile.yaml` to `.gitignore` (do this first).

### 2. `int_jobs_structured` (silver, incremental, prod-only)
- `AI.GENERATE` (or current equivalent — verify) with `output_schema` emitting typed fields:
  `seniority`, `years_experience_min`, `required_techs` (array), `location_eligibility`,
  and `requirement_text` (requirements/industry only — the chatty portion is dropped).
- Input: `silver_jobs` survivors. Incremental key: `content_hash` with the
  `where content_hash not in (select content_hash from {{ this }})` guard —
  **this guard is a cost control (§5.5); never remove it.**
- Injection defense (§5.6): posting text wrapped in explicit delimiters framed as
  data-not-instructions.
- Failure semantics: null/failed generations land with `extract_ok = false`,
  retried next run (guard on `content_hash` + `extract_ok`), never silently
  dropped or scored.
- **Dev parity:** on the DuckDB target the model is a stub emitting the same
  columns as typed nulls (`enabled`/target-conditional SQL, pattern per §5).
  Downstream models and unit tests run against the stub.

### 3. `int_jobs_scored` (silver, incremental, prod-only)
- Scoring function: **evaluate `AI.SCORE` first** (GA 2026, natively managed — no
  resource connection to provision; rubric-in-prompt, rating output), falling back to
  `AI.GENERATE_INT` (also GA, needs the Vertex connection) if AI.SCORE can't express
  the 1–5 contract or its provenance needs. Whichever is chosen: temperature-0
  semantics, prompt = profile block (static prefix, from `var('profile_prompt')` —
  static so Gemini context caching discounts it) + the trimmed `requirement_text` —
  never the full posting.
- Columns: `fit_score` (1–5), `model`, `prompt_version`, `scored_at`.
- Same incremental guard; re-score is triggered by `content_hash` change or
  `prompt_version` bump.
- Workflow passes the rendered profile: a make target renders
  `config/profile.yaml` → `--vars` (add to `ingest.yml` dbt-prod step).
- Range validation: `accepted_values` on 1–5 (out-of-range = flagged, not delivered).

### 4. Gold + digest become score-aware
- `fct_job_postings` gains `fit_score`, `prompt_version`, `scored_at` (nullable —
  a not-yet-scored posting still ships). Out-of-range scores are nulled here
  (flagged upstream), so delivery never shows a bogus number.
- `deliver/digest.py`: order `fit_score desc nulls last`, then the existing
  soft signals; show the score (e.g. `[fit 4/5]`); unscored postings say so.
  Digest tests extend the existing DuckDB-seeded pattern.

### 5. Docs
- ARCHITECTURE §5 → "as built"; roadmap V2 → done; TODO sweep; note the
  first-backfill cost expectation (~$0.12, §5.5) and how to sanity-check it
  (row counts in `int_jobs_structured` vs silver survivors after run 1).

## Acceptance

- [ ] Full local suite green: `make check`, `dbt build --target dev` (stubs), prod `dbt parse`.
- [ ] Coverage gate untouched (≥85), no swallowed exceptions, mypy --strict clean.
- [ ] Unit tests: prompt rendering; stub columns; gold null-score passthrough;
      digest ordering with scores; out-of-range score nulled.
- [ ] Schema tests: `accepted_values` 1–5, `not_null` on provenance columns where scored.
- [ ] First prod run: backfill cost sanity-checked; incremental second run processes ~0 rows.
