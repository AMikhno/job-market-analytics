# 0030 — Workday: filter the list by location on Workday's side, detail-fetch only new postings

**Status:** accepted (2026-11-02). Builds the Workday source that ADR-0013 deferred, under
ADR-0021's description rule; supersedes the "not built" recommendation in
`docs/research/workday-ref-discovery.md`.

## Context

Workday was deferred for its cost, not its difficulty. A board's list is a paginated POST of 20
postings a page that carries no description, so ADR-0021 makes every posting a second call, and
on a large board that is thousands of serial requests to one host. Re-probing a large public
board on 2026-11-02 sharpened the picture:

- **The unfiltered list cannot reach a large board at all.** It stops at 2000 postings. That
  board's facet counts sum to about 2,700, so fetching "everything" silently drops a quarter.
- **`total` is only reported on page 1**; later pages say 0. A walk that re-reads it per page
  ends after two pages without an error.
- **Every list response carries the board's facets**, with an id per value: job category, job
  type, time type, location type (office/remote), country and site. Applying them in the request
  body filters server-side — the totals change accordingly.
- **The list item is five fields**: title, `externalPath`, a location summary that becomes a
  count for multi-location postings ("5 Locations"), relative date text ("Posted Today") and a
  requisition id. Everything ADR-0021 needs, and the real locations, come from the detail.

## Decision

**1. Filter by location in the request, using the board's own facet ids.** The allowed-location
rule already removes postings in silver (ADR-0015), so asking Workday for only those places drops
nothing silver would keep, and it cuts list pages and detail calls together. The rules are
private config (`WORKDAY_LOCATIONS`) because a filter rule is a preference (ADR-0028). Each rule
names countries and, optionally, location types; the adapter matches them by name against each
board's facets on every run, because the ids differ per tenant.

Facets AND across parameters and OR within one, so each rule is its own query and the results are
merged by `externalPath` — a multi-location posting can satisfy two rules and is kept once.

**2. Fail closed, never widen.** With no rules the adapter refuses to fetch. A rule whose country
or location type is absent from a board's facets is dropped for that board (facets only list
values that have postings), never retried without the missing part. A board on which *no*
configured country appears is a failed board, not an empty one: that is also what a facet layout
the adapter cannot read looks like, and it must reach the run's skipped boards.

**3. Detail-fetch only postings the warehouse has not described yet.** Once per run, the pipeline
reads the Workday postings already landed with a description and passes them to the adapter,
which lands those from the list alone. `stg_workday__jobs` carries each posting's last described
landing forward onto those rows, so silver reads a full row exactly as before.

This is not the "land list-only rows and enrich later" option ADR-0021 rejected. That option put
description-less rows in silver; here a list-only row is only ever landed for a posting whose
description is already in the warehouse, and bronze fills it before silver reads it. A new
posting is detail-fetched in the run that first sees it, and a detail with no description still
raises.

**4. Land every location of a posting.** The detail's primary `location` and its
`additionalLocations` are joined into one field, so silver's word match keeps a posting if any of
its places is allowed.

## Rejected

- **A per-run cap on detail calls.** It would bound the first run, but by deferring postings
  rather than excluding them, so a capped board lands an arbitrary subset. The location filter
  bounds the same cost by excluding exactly what silver would drop.
- **Filtering on job category.** Categories are each tenant's own taxonomy, so a rule written
  for one board means nothing on the next. On the measured board one category held 85% of
  postings, and the postings mentioning analytics were spread across 13 of its 15 categories, so
  a category filter would either save little or drop relevant postings. An exclude list remains
  possible later as one more facet in the same request.
- **Title filtering before the detail call.** Titles rank; they do not remove (ADR-0015,
  ADR-0023).

## Consequences

- **Cost follows new postings, not board size.** On the measured board the location rules cut
  the first run from roughly 2,100 requests to the few hundred postings in the allowed places,
  and later runs to the postings that are new since the previous one.
- **Description edits to a known posting are not picked up.** Only the list is re-read for it.
  When the raw partition holding a posting's described landing expires
  (`bq_raw_partition_expiry_days`), the next run detail-fetches it again, so the gap closes
  itself.
- **A board whose filtered list still reaches 2000 postings logs a warning**: the cap is a hard
  limit of the API, and the only remedy is a narrower rule.
- **One run-time read per run** of the Workday raw table — the only adapter that depends on
  warehouse state. Every other source's adapter is built exactly as before.
- **Workday refs must be complete before a row is switched on.** Discovery records the tenant
  only; an active row without its shard and site fails the whole run at load time, so the
  website-scrape pass in `docs/research/workday-ref-discovery.md` comes first.
