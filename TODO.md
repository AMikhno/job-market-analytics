# TODO

## V1.5 — broaden ingestion + filtering — ✅ COMPLETE

All shipped and verified (see `docs/decisions/0013`–`0016`):

- [x] Ashby ATS adapter (public keyless GET) — `ingest/adapters/ashby.py`
- [x] Per-source `board_ref` validation (fail-loud at load) — ADR-0012
- [x] Separate BigQuery datasets per zone (`jobs_bronze/_silver/_gold`) — ADR-0014
- [x] Ingestion completeness: `first_seen_at` ("new since last run") + documented model
- [x] Desired technologies + titles as **soft** signals (`desired_tech_hits`, `title_match`) — ADR-0015
- [x] Inactive-postings retention: silver is the record, gold is live-only — ADR-0016
- [x] `make validate-companies` pre-flight helper + expanded example list

## V2 — tentative

### More ATS adapters (tentative)
- [ ] Generalized adapter/HTTP contract (POST + offset pagination + per-job detail) — ADR-0013
- [ ] BambooHR adapter (`careers/list` + per-id detail; undocumented, add fragility guard)
- [ ] Workday adapter (POST + pagination + detail; `board_ref` = tenant/wdN/site) — needs the
      generalized contract above
- [ ] iCIMS — deferred: no public keyless API (OAuth2 feed or brittle scraping); stays
      inventory-only (`active=false`) until we accept one of those costs — ADR-0013

### Sourcing evaluation (tentative) — see ADR-0017 / `docs/research/openjobdata.md`
- [ ] Verify **openjobdata** before adopting: Ottawa/Canada density (decisive — needs a real
      parquet pull + filter/count), dataset license/ToS, source identity, cadence, lifecycle mapping
- [ ] If adopted: new `openjobdata` source (filter delta parquet → `raw_openjobdata_jobs` →
      `stg_openjobdata__jobs` → same silver/gold). Hybrid — niche local ATS still need custom collection
- [ ] Decide fate of the Greenhouse/Lever/Ashby adapters (keep vs retire) — after coverage verified
- [ ] Company-discovery notebook: add under `tools/company_discovery/` and exclude `tools/` from the
      CI gates (ruff/mypy/coverage in `pyproject.toml` + `.pre-commit-config.yaml`) — ADR-0018

### Filtering (tentative)
- [ ] Revisit whether any soft signal should become a hard filter, once the LLM can judge fit

> AI relevance (LLM extraction/scoring, embeddings, relevant-links delivery) is the core of V2 —
> see `ARCHITECTURE.md` §5 and §9.

## Operational (ongoing, human-owned)
- [ ] Expand the actual company list in the GitHub Actions variable (`COMPANIES_CSV_CONTENT`) —
      secrets boundary; validate with `make validate-companies` before pasting
