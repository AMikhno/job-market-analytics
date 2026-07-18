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

## V2 — AI relevance (scoped, ready to build)

**Scope fixed by ADR-0020; implementation contract in `docs/v2-plan.md`** — execute its
work items top-to-bottom, one conventional commit each:

- [ ] Profile config: `shared/profile.py` + `config/profile.example.yaml` + prompt rendering
      (`PROMPT_VERSION` provenance); gitignore guard for the real file
- [ ] `int_jobs_structured` — AI.GENERATE typed extraction, content_hash incremental guard
      (cost control), delimiter injection defense, dev-target stub
- [ ] `int_jobs_scored` — AI.GENERATE_INT 1–5 fit score, profile as static prefix,
      model/prompt_version/scored_at provenance, accepted_values test
- [ ] Gold + digest score-aware: fit_score orders (never filters — ADR-0020), unscored
      postings still ship
- [ ] Docs to "as built"; verify first-backfill cost (~$0.12 expected, §5.5)

## Parked (gated — not V2)

- **More ATS adapters** (generalized POST/pagination contract, BambooHR, Workday; iCIMS
  inventory-only) — ADR-0013; may be subsumed by openjobdata
- **openjobdata evaluation** — decisive gate: real Ottawa-coverage parquet pull; then
  license/identity/cadence/lifecycle — ADR-0017 / `docs/research/openjobdata.md`
- **Embeddings** (cost pre-filter, cross-source dedup) — deferred, no current payoff — ADR-0020
- **Company-discovery notebook** under `tools/` (CI-quarantined) — ADR-0018
- **Soft-signal → hard-filter revisit** and score thresholds — V3 feedback loop

## Operational (ongoing, human-owned)
- [ ] Expand the actual company list in the GitHub Actions variable (`COMPANIES_CSV_CONTENT`) —
      secrets boundary; validate with `make validate-companies` before pasting
- [ ] Create the digest secrets in the `production` environment: `SMTP_USER` +
      `SMTP_PASSWORD` (Gmail app password; https://myaccount.google.com/apppasswords),
      optional `DIGEST_TO` variable. Until set, the digest step logs "disabled" and skips.
