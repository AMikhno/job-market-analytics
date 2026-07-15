# 0010 — Three medallion zones, not five layers; per-model materialization

**Status:** accepted

The pipeline reads as bronze → silver → (V2: structured → scored) → gold, which looks like five
layers but is **three dbt zones**: bronze/silver/gold map one-to-one onto dbt's
staging → intermediate → marts. Silver is the intermediate zone, which dbt expects to hold several
chained models. The V2 AI models (`int_jobs_structured`, `int_jobs_scored`) are intermediate models
that live in `models/silver/` — **not** new top-level layers and **not** a separate `intermediate/`
folder (we keep one vocabulary: medallion).

This is idiomatic, not an anti-pattern, because every model does one distinct job (no `select *`
passthrough layers). The risks of multi-model zones are handled explicitly:

- **Materialization is per-model**, so we don't persist redundant copies:
  - bronze `stg_*` → **view**
  - `int_jobs__unioned` → **view** (originally ephemeral; amended 2026-07 because dbt
    unit tests must introspect input columns from the warehouse, and an ephemeral
    model has no relation there — a view costs nothing and keeps the DAG debuggable)
  - `silver_jobs` → **table**
  - `int_jobs_structured` / `int_jobs_scored` (V2) → **incremental** (LLM output is never recomputed)
  - `fct_job_postings` → **table**
- **One naming system.** Medallion folder names only; no mixing with a dbt `intermediate/` folder.
