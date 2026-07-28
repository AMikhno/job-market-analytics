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

## V1.8 — Tier 1 ATS adapters — planned (separate branch)

**Survey + evidence: `docs/research/ats-feeds.md`; re-probe with
`tools/company_discovery/ats_feed_probe.py` before starting.** Seven ATS meet ADR-0013's
public-keyless bar *today* and are single-GET/JSON — the Ashby pattern, not the heavier
POST/pagination contract. Counts below are post-re-audit: **53 inactive companies**, a 43%
increase on the 123 boards now running.

- [ ] **BambooHR** — `{ref}.bamboohr.com/careers/list` → `{meta, result[]}`. **33 companies**,
      the best payoff-to-effort work in the project. No per-id detail call needed
- [ ] **Recruitee** (`{ref}.recruitee.com/api/offers/`, 6) + **Workable**
      (`apply.workable.com/api/v1/widget/accounts/{ref}?details=true` — the v1 widget; the
      documented v3 path 404s, 5)
- [ ] **Rippling** (3), **BreezyHR** (2), **Pinpoint** (2), **SmartRecruiters** (2 — the only
      one needing `limit`/`offset` pagination) — nearly identical, do together
- [ ] Each: sanitized committed fixture + adapter tests + `active=true` flip for its rows.
      The real cost is the `RawPosting` field mapping, not the HTTP call
- [ ] Verify a **second** ref per platform first — one company's board can be misconfigured
      in ways that look platform-wide

Deferred, unchanged: **Workday** (endpoint live — 422 not 404 — but needs tenant/wdN/site
captured for all **30** rows before any code). Not keyless, stays inventory: SuccessFactors
(401), Teamtailor (API key, now 6 companies), iCIMS, JazzHR, UKG, Dayforce, ADP, Phenom, Indeed.

## Next session — start here

1. **Check the first prod run with 123 boards** (Actions → Ingest). First time in prod for the
   Ashby percent-encoding, the Lever EU fallback, and the healthchecks step. Expect a much
   larger ingest; watch for a `Skipped boards (redacted:…)` annotation and resolve any with
   `make whois REF=…`
2. **Value/coverage check against real gold data** — now the highest-value open question.
   Coverage went 13 → 123 boards, but most new companies are US-only and the silver location
   gate drops non-Canadian postings, so 8,885 visible postings will *not* become 8,885 gold
   rows. Count: active gold postings, title-matched, and how many you'd actually apply to.
   **If the funnel is still thin after this much coverage work, V2 should be relevance, not
   more sources** — and these numbers feed the README results section either way
3. Then either **V1.8** (above) or **V2** (below), depending on what step 2 says
4. Housekeeping: arm healthchecks (`NOTES.local.md` §4); delete the merged
   `chore/uv-and-permissions` and `feat/company-list-correctness` remote branches; clear the
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
      `make update-company-list` validates it first
- [x] Digest secrets created in the `production` environment (`SMTP_USER` + `SMTP_PASSWORD`).
      `DIGEST_TO` stays optional — unset, the digest mails `SMTP_USER` (`deliver/digest.py`).
- [ ] Create the healthchecks.io check and add its ping URL as the `HEALTHCHECK_URL` **secret**
      in the `production` environment (period 1 day, grace ≥ 6h — twice-daily cron plus DST
      drift). Until set, the step logs "disabled" and skips; the switch is not armed.
