# TODO

## V1.5 — broaden ingestion + filtering

See `docs/decisions/0013`, `0014` and the plan discussion for context.

### Done
- [x] Ashby ATS adapter (public keyless GET) — `ingest/adapters/ashby.py`
- [x] Validate company `board_ref` values (per-source, fail-loud at load) — ADR-0012
- [x] Separate BigQuery datasets per zone (`jobs_bronze/_silver/_gold`) — ADR-0014
- [x] Ingestion completeness: `first_seen_at` ("new since last run") + documented model
- [x] Desired technologies + titles as **soft** signals (`desired_tech_hits`, `title_match`) — ADR-0015
- [x] Decided where inactive postings live: silver is the record, gold is live-only — ADR-0016
- [x] `make validate-companies` pre-flight helper + expanded example list covering the new ATS shapes

### Ingestion — next
- [ ] Generalized adapter/HTTP contract (POST + offset pagination + per-job detail) — ADR-0013
- [ ] BambooHR adapter (`careers/list` + per-id detail; undocumented, add fragility guard)
- [ ] Workday adapter (POST + pagination + detail; `board_ref` = tenant/wdN/site) — needs the
      generalized contract above
- [ ] iCIMS — **deferred**: no public keyless API (OAuth2 feed or brittle scraping); stays
      inventory-only (`active=false`) until we accept one of those costs — ADR-0013
- [ ] Expand the actual company list in the GitHub Actions variable (`COMPANIES_CSV_CONTENT`) —
      human-owned (secrets boundary); validate with `make validate-companies` before pasting

### Search/filtering
- [ ] (V2) Revisit whether any soft signal should become a hard filter, once the LLM can judge fit
