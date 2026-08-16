# 0026 — The warehouse lives in us-central1, so the AI functions can reach a model

**Status:** accepted

V2 runs relevance scoring as dbt SQL against warehouse-native AI functions (ADR-0004). That
requires a model the warehouse can actually call, and the original location could not call one.

Three measurements, taken 2026-08-15/16, decided it:

- **No foundational model is served in `northamerica-northeast2`** — `AI.SCORE` and `AI.GENERATE`
  both fail there with the same internal error across three runs, while `AI.SCORE` returns a rating
  in `US` and `us-central1` (ADR-0025 records the endpoint-level probes behind this).
- **No embedding model is served there either.** `text-embedding-005`, `gemini-embedding-001` and
  `text-multilingual-embedding-002` all return 404 in `northamerica-northeast2` and 200 in
  `us-central1`. So the constraint is the region, not the choice between generative and vector
  approaches — neither was available.
- **A query cannot read across locations**: `Dataset jobs_gold was not found in location
  us-central1`. Co-location is enforced, not advisory, so scoring could not sit in one region and
  read silver from another.

**Decision: move the warehouse to `us-central1`.** It is verified to serve both `AI.SCORE` and all
three embedding models, so the location does not pre-commit V2 to either approach. No residency
requirement applies — the pipeline reads public job postings. Canada's in-region Gemini processing
exists only in `northamerica-northeast1`, which serves neither the chosen model nor any embedding
model tried, so there was no Canadian option to weigh against this one.

**Rejected alternatives.** Scoring in a Python step would have kept the original region, since an
HTTP call to Vertex is not subject to BigQuery's co-location rule — but it drops the in-SQL
property ADR-0004 chose deliberately. A BigQuery remote function over Cloud Run would have kept
both the region and the in-SQL property, at the cost of a service to deploy, authenticate and keep
alive; silent decay in an unattended component is the failure mode this repo is built against
(ADR-0013).

**History survived because it was never in the partition metadata.** `first_seen_at` is derived in
silver as `min(ingested_at)` over each `job_key`, and `ingested_at` is a stored column. Exporting
raw to Parquet and reloading therefore preserves "when was this posting first seen" exactly, which
is what made a migration acceptable at all. Verified by diffing row counts for all 11 tables
against a pre-migration baseline: identical.

Two consequences worth stating. The raw tables are ingestion-time partitioned, and that metadata
does not survive a Parquet round-trip — so the 180-day expiry clock restarts at reload. It is a
storage-cost control, not a correctness property, so restarting it costs a few months of extra
retention on one cohort and nothing else. And the derived zones were not migrated at all: bronze,
silver and gold rebuild from raw on the next run, and the seeds reload from `dbt/seeds/`, which is
the rebuilds-not-migrations property in [ARCHITECTURE.md](../../ARCHITECTURE.md#rebuilds-not-migrations)
being cashed in rather than merely asserted.
