# Documentation

This is the written half of the project: how the pipeline works, why it is shaped the way it is,
and the measurements behind those choices. The code is on
[GitHub](https://github.com/AMikhno/job-market-analytics); the model-level lineage is in the
[dbt docs](https://amikhno.github.io/job-market-analytics/lineage/).

## Where to start

**[Architecture](ARCHITECTURE.md)** is the system as it runs today: three dbt zones, two
warehouses, the automation split by permission scope, and the four layers of health checks.
If you read one page, read [the failures that look like health](ARCHITECTURE.md#failures-that-look-like-health)
— a scheduled pipeline's worst failures produce no signal at all, and each one needed its own gate.

**Decisions** are the *why*. Each record states what was decided, what it cost and what it ruled
out. Three that shaped the most:

- [Three medallion zones, not five layers](docs/decisions/0010-three-zones-not-five-layers.md) —
  bronze, silver and gold map one-to-one onto dbt's staging, intermediate and marts.
- [Dual-target dbt: DuckDB dev, BigQuery prod](docs/decisions/0002-dual-target-duckdb-bigquery.md)
  — one SQL codebase, dialect differences isolated in `adapter.dispatch` macros.
- [The score orders, never filters](docs/decisions/0020-v2-scope-relevance-only.md) — why the
  AI layer can fail without the pipeline shipping nothing.

**Research** is the evidence: probes against each ATS, a cost model for adding sources, and a
measured account of which signals actually predict relevance.

- [ATS feed survey](docs/research/ats-feeds.md) — which platforms qualify for an adapter, and why.
- [What actually predicts relevance](docs/research/relevance-signals.md) — what V2 has to encode.
- [Ingestion cost as sources multiply](docs/research/ingestion-cost.md) — the general model.

**[V2 plan](docs/v2-plan.md)** is the implementation contract for the AI layer: in-warehouse
extraction and scoring as dbt models.
