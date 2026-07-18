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
  - `int_jobs__unioned` → **view** (originally ephemeral; amended 2026-07: a view is
    operationally strictly better here — debuggable in the warehouse (`select * from
    int_jobs__unioned` when silver looks wrong), visible in `information_schema` and
    lineage, and free: the engine scans the same bytes whether the union is a view or
    an inlined CTE. Ephemeral saves only namespace clutter, and it also blocks dbt
    unit tests, which introspect input columns from a real relation. We use ephemeral
    nowhere; a model either earns a name in the warehouse or shouldn't exist)
  - `silver_jobs` → **table**
  - `int_jobs_structured` / `int_jobs_scored` (V2) → **incremental** (LLM output is never recomputed)
  - `fct_job_postings` → **table**
- **One naming system.** Medallion folder names only; no mixing with a dbt `intermediate/` folder.
