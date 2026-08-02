# Workday — probe results and ref discovery

**Status:** measured findings, 2026-07-29. **Supersedes the Workday row in `ats-feeds.md`**, which
recorded a reading these probes disproved.
**Nothing is built.** Workday remains inventory-only (`active=false`, 30 companies).

## The correction: 422 does not mean "the endpoint is live"

`ats-feeds.md` recorded *"returned **422, not 404** — the endpoint is live and keyless."* That
inference is wrong.

`POST /wday/cxs/{tenant}/{site}/jobs` returned **422 for every combination tried**, including
tenant × site × shard combinations that cannot possibly be right. Meanwhile **3 of 4 known-good
public boards return 200 with jobs on the identical request shape**. So the request shape is fine
and **422 is simply the uniform reply to any unknown ref**.

The consequence is what matters: **the search space gives no feedback.** A wrong guess and a
nearly-right guess are indistinguishable, so brute-forcing the site segment is hopeless. This is
the opposite of the Greenhouse/Lever/Ashby case, where a 200-vs-404 signal made the API probe in
`tools/company_discovery/ats_audit.py` work.

Two related dead ends:

- **`*.myworkdayjobs.com` is wildcard DNS.** `definitely-not-a-real-tenant-xyz99.wd1` resolves, so
  DNS cannot identify the shard either.
- The shard root **406s** with no scrapeable HTML.

## What does work: scraping the company's own website

A crude one-hop scraper over the company website recovered **9 of 12** refs:

| Company | Recovered ref (`tenant/shard/site`) |
|---|---|
| REDACTED | `REDACTED/wd3/REDACTED` |
| REDACTED | `bb/wd3/REDACTED` |
| REDACTED | `cae/wd3/career` |
| FCC | `REDACTED/wd3/careers-carrieres` |
| REDACTED | `REDACTED/wd3/hydro_ottawa_careersite` |
| Incognito | `REDACTED/wd3/Incognito` |
| REDACTED | `REDACTED/wd1/REDACTED` |
| REDACTED | `REDACTED/wd5/Jabil_Careers` |
| REDACTED | `REDACTED/wd3/LCBOCareerSite` |

**Site names have no pattern whatsoever** (`career`, `careers-carrieres`, `LCBOCareerSite`,
`hydro_ottawa_careersite`) and shards vary across wd1/wd3/wd5. That is precisely why guessing
fails — and it is the clearest vindication so far of keeping `website` as a column
(CLAUDE.md: the recovery key). **All stored tenants were correct**; only the site and shard
segments were missing.

Expect roughly 75% automatic recovery, ~8 of 30 rows needing a human with a browser.

## Workday clears the ADR-0021 description gate — via detail

- **List** gives `title`, `externalPath`, `locationsText`, `postedOn`, `bulletFields`.
- **Detail** gives 4–7.5 KB of `jobDescription` HTML, plus `externalUrl` and `startDate`.

Two mapping traps, both of which would silently corrupt data if missed:

1. **`postedOn` is relative text** ("Posted Today", "Posted 30+ Days Ago"). The real date is
   `startDate`, from the detail call.
2. **`locationsText` can be a count**, e.g. `"12 Locations"`. Real location also comes from detail.

## Why it is not built: the runtime cost

The list pages at **limit=20** and `total` caps at 2000.

| Board | Postings | Requests |
|---|---:|---|
| REDACTED | 81 | 5 list POSTs + 81 detail GETs |
| REDACTED | 2000+ | 100 list POSTs + **2000 detail GETs** |

At the configured 0.5 s per-host interval, REDACTED alone is **~17 minutes of serial fetching** on
its own host. That is the Renesas problem again — one board that cost ~10 of an 11.5-minute run
to yield 2 gold postings.

An adapter itself is cheap. Measured across the nine shipped sources, a new source is **~250–400
lines total** (adapter + test + ~10 lines in `ingest/sources.py` + a one-line staging model + a
`sources.yml` entry + a `bronze.yml` test block). Workday is Class C — POST body, offset
pagination, multi-segment ref — but well understood. **Writing the adapter is not the blocker;
feeding it and paying its runtime is.**

## Junk refs found in the inventory while probing

Discovery matched the wrong URL segment for several rows. These are list-repair items, not adapter
work:

- **Teamtailor** stores `app`, `na`, `www` for four companies — doubly dead, since Teamtailor also
  requires `X-Api-Key`.
- **Eightfold** stores `app` for one company.
- **Jobvite is 0/4** — the websites show no Jobvite board at all, which suggests those rows are
  **stale, not blocked** (the companies moved ATS).
- **Paylocity** refs *are* recoverable and turn out to be GUIDs
  (`2d4c79d4-b82f-466c-…`), but the v2 API path returns HTML, so the endpoint remains unknown.

## Standing recommendation

Do not build Workday yet. If it is built later, do the website-scrape ref pass first and gate the
adapter to a subset of tenants rather than all 30 — the large-enterprise boards are exactly the
profile with the worst measured conversion (`triage-to-shortlist.md`).

Probe scripts: `tools/probes/workday/`.
