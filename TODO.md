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

## V1.7 — company-list correctness — ✅ COMPLETE (2026-07-28, PR #12)

Triggered by auditing the list against the live APIs: of 157 active boards, **101 were
404ing** and nothing said so. Three ingest bugs and a broken discovery loop.

- [x] Ashby board refs may contain inner spaces (`Dominion Dynamics`) — own pattern +
      percent-encoding. Was worse than a missed board: `load_companies` validates *before*
      fetching, so such a row would have hard-failed the whole run
- [x] Lever EU shard (`api.eu.lever.co`) — adapter falls back on 404 only; region is a
      property of the board, not the company, so it stays out of the list
- [x] **Skipped boards are no longer silent** — a 404 left its source `status="ok"`, so the
      digest reported "all sources healthy" while a company dropped out. Now redacted at
      write time and surfaced in the CI annotation, step summary and digest footer;
      `make whois REF=…` resolves one locally
- [x] `website` column (recovery key, not pipeline input); CI gets an **active-only
      projection** (`make companies-variable`) — a variable caps at 48 KB
- [x] Restaging **merges** instead of overwriting — hand-fixed refs survive a refresh
- [x] Discovery state moved to `config/discovery/`; `make discover` is the entry point
- [x] Discovery finds boards it used to miss: deeper hop past marketing pages, raw-HTML
      scan, API-probe fallback. Regression set went 3/10 → 10/10
- [x] **873-company re-audit + master rebuilt.** The old tool discarded every company whose
      ATS it couldn't see (575 of 724), which is why the list held 141 rows. Result: **285
      rows, 123 active boards, all verified resolving, 8,885 postings visible** (was 13
      active / ~500). Variable pushed; merged to main as PR #12
- [x] Companies on a non-V1 ATS stay as `active=false` inventory rows with their real ATS

## V1.8 — Tier 1 ATS adapters — ✅ COMPLETE (2026-07-28, branch `feat/tier1-ats-adapters`)

Six of the surveyed seven shipped; **161 active boards, up from 123**. See ADR-0021/0022 and the
"As built" section of `docs/research/ats-feeds.md`.

- [x] **BambooHR** (33 rows, 32 resolving) — list + per-posting detail
- [x] **Recruitee**, **Workable**, **Pinpoint** — single GET, description in the list
- [x] **Rippling** (collapses its per-location duplicate rows) and **SmartRecruiters**
      (`limit`/`offset` pagination) — both list + detail
- [x] **A V1 source must yield a description** (ADR-0021). Three of the six omit it from the
      list, and silver's deal-breaker filter + desired-tech signal both read it, so a list-only
      row would be permanently unfilterable. Verified: **0 empty descriptions** in 1,324 postings
- [x] **Parallel board fetch, per-host rate limiting** (ADR-0022) — needed once requests scale
      with postings rather than boards
- [x] Sanitized fixtures + adapter tests for each; every platform re-probed against a second
      live ref first (which is what caught the detail-call, duplicate-row and no-date surprises)
- [x] `make validate-companies` no longer fails on an *inactive* row with a blank ref — shipping
      an adapter used to retroactively invalidate the inventory rows already on that ATS

**BreezyHR moved to V1.9** (below). Workday unchanged: endpoint live (422, not 404) but needs
tenant/wdN/site captured for all 30 rows, plus a POST paginator. Not keyless, stays inventory:
SuccessFactors (401), Teamtailor (API key), iCIMS, JazzHR, UKG, Dayforce, ADP, Phenom, Indeed.

## V1.9 — company-list repair, then BreezyHR

Verifying V1.8 against live boards turned up list problems that no adapter can fix. **13 of the
51 Tier 1 rows were left `active=false`**; each needs a human or a discovery re-run:

- [ ] **Recruitee is mis-mapped, all 6 rows.** `career` (3 rows: Field Effect, RBR Global,
      Trafilea) 404s — it is a URL path fragment, not a subdomain. Worse, **`huaweicanada` is
      attached to *CMC Microsystems* and to a "Kanata North portal" row**: activating either
      would land Huawei's 167 postings under another company's name. `certus` (TPC Training)
      resolves but is unverified. Re-run discovery for these before touching `active`
- [ ] **Rippling: 3 blank refs** (Argyle, Buxton, Pythian). Pythian's real ref is `pythian` —
      verified live, 11 postings, 2 reaching gold. The other two need discovery
- [ ] **SmartRecruiters: neither row is usable.** Sectigo's `job-widget` is a stub (200,
      `totalFound: 0`). Renesas resolves but is a **poor trade**: 871 postings, ~10 minutes of
      the run on a shared host, **2** surviving the location gate. Decide deliberately
- [ ] **BambooHR `kivuto`** 302s to a non-JSON page (correctly skipped and reported)
- [ ] **Workable `Fidus Systems`** has no ref
- [ ] Add the `sources.yml` freshness block for `raw_rippling_jobs` / `raw_smartrecruiters_jobs`
      **in the same change that activates a real board** — freshness errors on an empty table and
      would take the whole prod run down with it
- [ ] **BreezyHR** (2 companies) — only if a description becomes reachable. Four keyless paths
      tried and documented in `docs/research/ats-feeds.md`; today the list alone would land
      untextable rows (ADR-0021)

## Next session — start here

1. **Push the rebuilt list and watch one prod run.** `gh variable set COMPANIES_CSV_CONTENT <
   config/companies.active.csv` (161 boards, 10 KB), then Actions → Ingest. First prod run for
   the parallel fetch, the per-host limiter and six new sources; watch the
   `Skipped boards (redacted:…)` annotation and the freshness gate
2. **Value/coverage check against real gold data** — still the highest-value open question, and
   the V1.8 numbers already point at an answer. Of **336 gold postings from the six new
   sources**, `title_match` was **0** and only 115 had any desired-tech hit: the new coverage is
   mostly software/other roles, not analytics. Re-run the count across *all* 161 boards, then
   decide. **If the funnel is still thin after this much coverage work, V2 should be relevance,
   not more sources**
3. Then **V2** (below), or the V1.9 list repair above if coverage still looks like the gap
4. Housekeeping: arm healthchecks (`NOTES.local.md` §4); delete merged remote branches; clear the
   superseded scratch files in `~/Downloads`

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

Step-by-step versions of these live in `NOTES.local.md` (gitignored personal runbook).

- [x] Enable GitHub Pages (Settings → Pages → Source: **GitHub Actions**) so docs.yml
      publishes the dbt docs site on pushes to main — done 2026-07-28
- [ ] Push the rebuilt company list: `gh variable set COMPANIES_CSV_CONTENT <
      config/companies.active.csv` (the **active-only projection**, never the master).
      `make update-company-list` validates it first. Now **161 boards / ~10 KB** after V1.8
- [x] Digest secrets created in the `production` environment (`SMTP_USER` + `SMTP_PASSWORD`).
      `DIGEST_TO` stays optional — unset, the digest mails `SMTP_USER` (`deliver/digest.py`).
- [ ] Create the healthchecks.io check and add its ping URL as the `HEALTHCHECK_URL` **secret**
      in the `production` environment (period 1 day, grace ≥ 6h — twice-daily cron plus DST
      drift). Until set, the step logs "disabled" and skips; the switch is not armed.
