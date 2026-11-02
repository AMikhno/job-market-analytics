# Workday — probe results and ref discovery

**Status:** measured findings, 2026-07-29, with a second probe on 2026-11-02
([below](#2026-11-02-facets-paging-and-the-detail-payload)). **Supersedes the Workday row in
`ats-feeds.md`**, which recorded a reading these probes disproved.
**The adapter is built (ADR-0030); the rows are not switched on.** Each still needs its full
tenant/shard/site ref, which is what the website scrape below recovers.

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

A crude one-hop scraper over the company website recovered **9 of 12** refs. The recovered values
themselves live in the private list, not here; what matters is their *shape*:

| Tenant segment | Shard | Site segment — the unguessable part |
|---|---|---|
| company name, sometimes abbreviated to 2 letters | `wd1` | same as the tenant |
| parent-company name, not the brand on the website | `wd3` | brand name, mixed case |
| name + a corporate suffix | `wd5` | `career` (singular) |
| | | `careers-carrieres` (bilingual, hyphenated) |
| | | `<name>CareerSite` (concatenated, mixed case) |
| | | `<name>_Careers` (underscored) |
| | | `<snake_case_name>_careersite` |

**Site names have no pattern whatsoever**, and shards vary across wd1/wd3/wd5. That is precisely
why guessing fails — and it is the clearest vindication so far of keeping `website` as a column
(CLAUDE.md: the recovery key). **All stored tenants were correct**; only the site and shard
segments were missing. One case is worth noting because no heuristic would ever produce it: a
tenant that is the *parent group's* name, with the site segment being the subsidiary's brand.

Expect roughly 75% automatic recovery, ~8 of 30 rows needing a human with a browser.

## Workday clears the ADR-0021 description gate — via detail

- **List** gives `title`, `externalPath`, `locationsText`, `postedOn`, `bulletFields`.
- **Detail** gives 4–7.5 KB of `jobDescription` HTML, plus `externalUrl` and `startDate`.

Two mapping traps, both of which would silently corrupt data if missed:

1. **`postedOn` is relative text** ("Posted Today", "Posted 30+ Days Ago"). The real date is
   `startDate`, from the detail call.
2. **`locationsText` can be a count**, e.g. `"12 Locations"`. Real location also comes from detail.

## The runtime cost, before server-side filtering

*This section is why Workday was deferred. The 2026-11-02 probe below found the facets that
remove most of this cost; ADR-0030 is the design that uses them.*

The list pages at **limit=20** and `total` caps at 2000.

| Board | Postings | Requests |
|---|---:|---|
| a mid-size tenant | 81 | 5 list POSTs + 81 detail GETs |
| the largest tenant in the list | 2000+ | 100 list POSTs + **2000 detail GETs** |

At the configured 0.5 s per-host interval, that largest tenant alone is **~17 minutes of serial
fetching** on its own host. That is the same problem as the one big SmartRecruiters board already
in the list, which cost ~10 of an 11.5-minute run to yield 2 gold postings.

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

## 2026-11-02: facets, paging and the detail payload

Re-probed on one large public board (the example board in `tools/probes/workday/`) before the
adapter was written.

- **Every list response carries the board's facets**, each value with an id and a count: job
  category, job type, time type, location type (office/remote), country and site. Passing ids in
  `appliedFacets` filters server-side: one category's id returned exactly that category's count
  as `total`. Different facets AND together and values within one facet OR.
- **The unfiltered list is capped at 2000.** The board reported `total = 2000` while its facet
  counts summed to about 2,700, so "fetch everything" cannot reach a quarter of that board.
- **`total` is only reported on page 1.** Page 2 of the same query returned `total = 0` with 20
  postings, so a walk that re-reads it per page ends after two pages.
- **Facets list only values that have postings**, which is why a missing country or location type
  on a board means "none there" rather than an error.
- **The detail carries every location**: `location` (the primary) plus `additionalLocations`.
  All five sampled remote postings listed `locationsText` as "N Locations", and their primary
  location was sometimes an office with the remote seats only among the additional ones.
- **Category is not a usable relevance filter.** The 15 categories are the tenant's own taxonomy.
  One held 85% of the board, and a full-text search for "analytics" matched postings in 13 of
  them.
- The pipeline's own User-Agent and a one-row list request (`limit: 1`, used to read the facets)
  are both accepted.

## Standing recommendation

Superseded by ADR-0030, which builds the adapter with server-side location filtering and detail
calls for new postings only. What still holds: **do the website-scrape ref pass before switching a
row on** — an active row with a tenant-only ref fails the whole run at load — and the
large-enterprise boards are still the profile with the worst measured conversion
(`relevance-signals.md`).

Probe scripts: `tools/probes/workday/`.
