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

## V1.8b — company-list repair (done 2026-07-28, same branch)

Verifying against live boards turned up list problems no adapter could fix, and a **root cause
in the discovery tool**: its API-probe fallback only knew Greenhouse/Lever/Ashby, so a company
on any of the six new ATS could never be recovered by probe — which is exactly why five boards
sat in the list with a blank or wrong ref while their APIs answered on the first guess.

- [x] **Probe every V1 endpoint** in `ats_audit.py`, not just three; `emit_ingestable`'s
      ATS→source map likewise (a stale entry there silently drops a supported company)
- [x] **`website` now survives discovery.** The audit cache always had it, but both inventory
      writers omitted the column, so all 285 master rows carried a blank recovery key —
      the one field `NOTES.local.md` and CLAUDE.md both describe as the way back after an ATS
      move. Backfilled 282/285 from the cache (the 3 misses are Brookstreet Hotel sub-venues)
- [x] **`career`, `careers`, `widget`, `job-widget` added to `_BAD_TOKENS`** — the exact junk
      refs that produced three 404ing Recruitee rows and a SmartRecruiters stub
- [x] **5 refs recovered and identity-checked against each board's own payload**: `pythian` +
      `argyle` (Rippling), `sectigo` (SmartRecruiters — 41 postings; the stored `job-widget`
      was a stub), `trafilea` (Recruitee, 43), `fidus` (Workable, 34). Renesas activated too
      (871 postings, ~10 min/run, 2 past the location gate — a deliberate call)
- [x] Freshness gates added for `raw_rippling_jobs` / `raw_smartrecruiters_jobs`, now that both
      have a verified board. **167 active boards**

## V1.9 — the rows discovery still can't place, then BreezyHR

**Re-discovery run 2026-07-28** (browser audit + API probe over all nine V1 endpoints) against
all seven. It fixed **one**; the reason for each of the other six is now recorded in the
master's `notes` so nobody repeats the same dead ends. **168 active boards.**

- [x] **TPC Training → `certus`, activated and renamed `Certus (TPC Training)`.** Their careers
      page links to `certus.com/careers`, i.e. the parent brand's Recruitee board — unlike the
      Huawei case, the board belongs to the same corporate group. Verified live, 6 postings
- [ ] **CMC Microsystems and Kanata North portal** — the browser reproduced `huaweicanada` from
      *both* independently. Both are ecosystem pages listing **member-company** jobs, which is
      the failure mode `tools/company_discovery/README.md` already warns about. CMC needs a
      human to find its own careers system; the portal row is a candidate for deletion (it is
      not an employer). The Huawei board itself is real and was the biggest contributor in
      testing (173 postings → 160 gold) if you ever want it as its own row
- [ ] **Field Effect, RBR Global** — Recruitee **confirmed** from their careers pages, but no
      board token is extractable and the API probe missed every guessable name. Cheapest fix is
      a human opening the careers page with the network tab and reading the board name off it
- [ ] **Buxton** — the crawl from `buxtonco.com` landed on `audiense.com/about/careers` (a
      different company) and found no token. Treat its detected ATS as unverified
- [ ] **Kivuto** — BambooHR confirmed from `kivuto.com/careers`, but
      `kivuto.bamboohr.com/careers/list` **302s to bamboohr.com**: the tenant is closed. There is
      no live board; this row cannot be fixed, only removed or re-pointed
- [ ] **BreezyHR** (2 companies) — only if a description becomes reachable. Four keyless paths
      tried and documented in `docs/research/ats-feeds.md`; today the list alone would land
      untextable rows (ADR-0021)

## Next session — start here

1. ~~Merge the branch first, then push the variable~~ — **the merge happened 2026-07-28, the
   variable push did not, and that took the pipeline down for four days** (2026-07-28 →
   2026-08-01). CI kept the pre-merge `main-safe` projection (122 boards, Greenhouse/Lever/Ashby
   only), so the six V1.8 sources got no companies, never ran, and their raw tables aged past the
   30h freshness gate. Every run then died at `make freshness` *before* `make deliver` — no
   digest for four days. Fixed by pushing `config/companies.active.csv` (**168 boards, ~13 KB**).
   **The step order in this item is still right; the failure was skipping step two.**
   - Guarded since (`fix/unconfigured-source-visibility`): a registered, active source that gets
     no boards from the list is now a warning in the run summary (`RunSummary.unconfigured`), the
     CI annotation, the step summary and the digest footer. It was previously an `INFO` log, so
     the footer read *"All 3 sources healthy (122 boards checked)"* — true, and useless — every
     run of the outage. Warn-only by design: shipping an adapter before its companies is a
     legitimate order of work, and the freshness gate still escalates after 30h
2. **Expect a ~11.5 minute run** (measured, 167 boards). Renesas alone is ~10 of those minutes:
   871 postings fetched one detail at a time on SmartRecruiters' shared host. A slow run is not
   a hung one. Deactivating that one row takes it back to a couple of minutes
3. **Storage: raw is append-only, and it adds up.** Measured **209 MB/run → 418 MB/day → 167 GB**
   logical steady state at the current 400-day partition expiry (free allowance: 10 GiB).
   Greenhouse + Ashby are 85% of it; ~39% of all bytes is description text stored twice (once in
   `raw`, once in `description_html`). Three cheap levers — stop duplicating text, expiry
   400 → 180 days, `storage_billing_model = 'PHYSICAL'` — take it to roughly 5–10 GB without
   changing what is fetched. Full analysis and a general proposal: **`docs/research/ingestion-cost.md`**
4. ~~Value/coverage check against real gold data~~ — **answered 2026-07-28, measured across all
   167 boards.** 10,170 postings fetched → **1,179 gold → 40 title-matched** (38 of those also
   hitting a desired tech). All 40 come from Greenhouse/Ashby/Lever; the six sources added in
   V1.8 contributed **316 gold postings and 0 title matches**. Keep rates invert with board
   size: BambooHR keeps 77% of what it fetches, Greenhouse 8%, SmartRecruiters 0.9%.
   **The constraint is relevance, not coverage — go to V2 scoring, not more adapters.**
5. **V2** (below). The V1.9 list repair is worth doing but is no longer the lever —
   it would add local boards, and local boards are already the ones that convert
6. Housekeeping: arm healthchecks (`NOTES.local.md` §4); delete merged remote branches; clear the
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
      `make update-company-list` validates it first. Now **168 boards / ~13 KB** after V1.8 —
      and only *after* the branch is merged (`NOTES.local.md` §3)
- [x] Digest secrets created in the `production` environment (`SMTP_USER` + `SMTP_PASSWORD`).
      `DIGEST_TO` stays optional — unset, the digest mails `SMTP_USER` (`deliver/digest.py`).
- [ ] Create the healthchecks.io check and add its ping URL as the `HEALTHCHECK_URL` **secret**
      in the `production` environment (period 1 day, grace ≥ 6h — twice-daily cron plus DST
      drift). Until set, the step logs "disabled" and skips; the switch is not armed.
