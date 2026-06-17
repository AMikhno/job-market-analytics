# 0002 — Dual-target dbt: DuckDB dev, BigQuery prod

**Status:** accepted

BigQuery is the production warehouse from day one; DuckDB is the dev target for free,
fast local iteration. There is **no migration** — prod is BigQuery from the first commit.
Cost: SQL must stay dialect-compatible; dialect-specific logic lives in dispatch macros.
V2's BigQuery AI functions are prod-only (stubbed on the dev target). This also keeps CI
secret-less (it runs on DuckDB), which is safe for a public repo's fork PRs.
