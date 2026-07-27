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
- [x] Dead-man's switch: successful runs ping healthchecks.io (`HEALTHCHECK_URL` secret), so a
      cron GitHub has suspended alerts instead of going silent — ARCHITECTURE §6

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

## Before starting V2 (sequencing — cheap checks that could re-scope it)

- [x] **Verify the first prod run on the V1.6 workflow** — runs, and is landing postings from
      real companies. SMTP secrets are set, so the digest sends (to `SMTP_USER` itself unless
      the optional `DIGEST_TO` secret is set)
- [ ] **Value/coverage check against real gold data**: how many active postings, how many
      title-matched, how many you'd actually apply to. If the funnel is thin, coverage —
      not scoring — is the priority. Same numbers feed the README results section
- [ ] **openjobdata Ottawa pull** (ADR-0017's decisive gate, one notebook): does the
      aggregated dataset see Ottawa/Canada AE postings the curated list misses? Answer
      re-scopes V2 if coverage beats relevance

## Operational (ongoing, human-owned)
- [ ] Enable GitHub Pages once (Settings → Pages → Source: **GitHub Actions**) so docs.yml
      can publish the dbt docs site on pushes to main
- [ ] Expand the actual company list in the GitHub Actions variable (`COMPANIES_CSV_CONTENT`) —
      secrets boundary; validate with `make validate-companies` before pasting
- [x] Digest secrets created in the `production` environment (`SMTP_USER` + `SMTP_PASSWORD`).
      `DIGEST_TO` stays optional — unset, the digest mails `SMTP_USER` (`deliver/digest.py`).
- [ ] Create the healthchecks.io check and add its ping URL as the `HEALTHCHECK_URL` **secret**
      in the `production` environment (period 1 day, grace ≥ 6h — twice-daily cron plus DST
      drift). Until set, the step logs "disabled" and skips; the switch is not armed.
