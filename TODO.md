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

## V1.6 — hardening + delivery — ✅ COMPLETE

All shipped (see ADR-0019 and `ARCHITECTURE.md` §9):

- [x] Seed terms matched literally (C++/C#/.NET safe) — regexp escaping in `regexp_word_ci`
- [x] Board-staleness rule: postings from removed/dead boards age out of gold (36h grace)
- [x] Strict adapter parsing: schema drift raises instead of landing 0 rows
- [x] Slack retired: GitHub-native failure email; warnings annotate + digest footer
- [x] Actions SHA-pinned; gitleaks runs in CI (local hook is bypassable)
- [x] Email digest of new postings (`deliver/digest.py`, watermark in `ops.digest_runs`)

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
- [ ] Create the digest secrets in the `production` environment: `SMTP_USER` +
      `SMTP_PASSWORD` (Gmail app password; https://myaccount.google.com/apppasswords),
      optional `DIGEST_TO` variable. Until set, the digest step logs "disabled" and skips.
