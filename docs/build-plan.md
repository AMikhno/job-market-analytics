# Build Plan

## V1 — MVP: ingestion + dbt transformations (current)
- [ ] `shared/` config, models, http, storage (DuckDB + BigQuery landing)
- [ ] Greenhouse + Lever adapters + source registry + pipeline entrypoint
- [ ] dbt: sources + freshness, bronze stg per source, silver (dedup, rule-filter), gold
- [ ] Seeds: companies, deal_breaker_tech, allowed_locations
- [ ] Tests: adapters vs fixtures, config, pipeline; dbt schema tests
- [ ] CI (DuckDB, no secrets) + scheduled ingest (WIF, freshness, Slack-on-failure)
- [ ] SQL linting via sqlfluff + the dbt templater (deferred — needs ref/source/macro resolution)
- [ ] `ops.ingest_runs` run-metadata table + zero-rows-is-failure check

## V2 — Relevance via AI, inside dbt
- [ ] `models/silver/int_jobs_structured` (`AI.GENERATE` + `output_schema`): typed fields + requirement_text
- [ ] Post-extraction deal-breaker filter (years, clearance, required-vs-nice-to-have)
- [ ] `models/silver/int_jobs_scored` (`AI.GENERATE_INT`, profile as static prefix, temperature 0,
      delimited untrusted input, `accepted_values` 1–5 test on fit_score)
- [ ] Embeddings (`AI.EMBED`/`AI.SIMILARITY`): cost pre-filter + cross-source near-dup dedup
- [ ] Relevant-links delivery; dev stubs for the AI models on DuckDB
- [ ] More sources

## V3 — Quality & breadth (direction)
- [ ] Feedback loop to calibrate the fit threshold
- [ ] Multiple profile embeddings (one per target role)
- [ ] Revisit paid APIs for ToS-restricted sources
