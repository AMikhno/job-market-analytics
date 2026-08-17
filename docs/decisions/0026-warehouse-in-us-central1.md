# 0026 — The warehouse lives in us-central1, so the AI functions can reach a model

**Status:** accepted

V2 runs relevance scoring as dbt SQL against warehouse-native AI functions (ADR-0004). That needs
a model the warehouse can call, and the original location could not call one.

Three measurements, 2026-08-15/16, decided it:

- **No foundational model is served in `northamerica-northeast2`** — `AI.SCORE` and `AI.GENERATE`
  both fail there with the same internal error across three runs, while `AI.SCORE` returns a
  rating in `US` and `us-central1` (ADR-0025 has the endpoint-level probes).
- **No embedding model either.** `text-embedding-005`, `gemini-embedding-001` and
  `text-multilingual-embedding-002` all return 404 there and 200 in `us-central1`. So the
  constraint is the region, not the choice between generative and vector approaches.
- **A query cannot read across locations**: `Dataset jobs_gold was not found in location
  us-central1`. Co-location is enforced, so scoring could not sit in one region and read silver
  from another.

**Decision: move the warehouse to `us-central1`.** It serves both `AI.SCORE` and all three
embedding models, so the location does not pre-commit V2 to either approach. No residency
requirement applies. Canada's in-region Gemini processing exists only in
`northamerica-northeast1`, which serves neither the chosen model nor any embedding model tried, so
there was no Canadian option to weigh against this one.

**Rejected alternatives.** Scoring in a Python step would have kept the original region, since an
HTTP call to Vertex is not subject to co-location — but it drops the in-SQL property ADR-0004
chose deliberately. A BigQuery remote function over Cloud Run would have kept both, at the cost of
a service to deploy, authenticate and keep alive; silent decay in an unattended component is the
failure mode this repo is built against (ADR-0013).

**History survived because it was never in the partition metadata.** `first_seen_at` is derived in
silver as `min(ingested_at)` over each `job_key`, and `ingested_at` is a stored column, so
exporting raw to Parquet and reloading preserves it exactly. Verified by diffing row counts for
all 11 tables against a pre-migration baseline: identical.

**Two GCS buckets came out of the move**, and they are not equivalent:

| Bucket | Location | Keep? |
|---|---|---|
| `job-search-pipeline-prod-raw-archive` | us-central1 | **Yes** — 674 Parquet objects, ~1.4 GiB compressed, a BigQuery-independent copy of raw and ops as of the migration |
| `job-search-pipeline-prod-migrate-ne2` | northamerica-northeast2 | No — export staging; disposable |

The archive is a one-off snapshot, not a maintained landing zone: nothing refreshes it. Turning it
into a standing Parquet layer is a separate decision, and one the measured duplication argues
against — raw holds each posting ~24 times by design.

Two further consequences. Raw tables are ingestion-time partitioned and that metadata does not
survive a Parquet round-trip, so the 180-day expiry clock restarts at reload; it is a storage-cost
control, not a correctness property. And the derived zones were not migrated at all — bronze,
silver and gold rebuild from raw on the next run and the seeds reload from `dbt/seeds/`, which is
[ARCHITECTURE.md](../../ARCHITECTURE.md#rebuilds-not-migrations) being cashed in rather than
merely asserted.
