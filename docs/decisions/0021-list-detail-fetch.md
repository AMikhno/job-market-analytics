# 0021 — A V1 source must yield a description, even if that costs a second call

**Status:** accepted (2026-07-28). Extends ADR-0013's "public, keyless feed" bar; supersedes the
assumption in `docs/research/ats-feeds.md` that all seven Tier 1 platforms are single-GET.

## Context

Six of the seven Tier 1 ATS were surveyed as "the Ashby pattern verbatim — one GET, map each
item". Re-probing them before writing the adapters showed that only three actually put the
posting's description in the list response:

| Has a description in the list | Description only via a detail call | No keyless description at all |
|---|---|---|
| Recruitee, Workable, Pinpoint | BambooHR, SmartRecruiters, Rippling | BreezyHR |

That distinction matters more than the extra HTTP call. Everything V1 knows about a posting
beyond its title comes from `description_html`: silver strips it to `clean_text` and runs both
the **deal-breaker filter** and the **desired-tech soft signal** over that text
(`dbt/models/silver/silver_jobs.sql`). A posting landed without one is not merely thinner — it
is permanently unfilterable. It can never match a deal-breaker (so it always survives), always
scores `desired_tech_hits = 0`, and would reach the digest on title and location alone. With
BambooHR alone accounting for 33 of the 51 companies, accepting description-less rows would have
meant most of the newly ingested inventory being invisible to the rules the pipeline exists to
apply.

## Decision

**A source qualifies for V1 only if a posting's description is reachable without credentials.**
Where that requires a per-posting call, the adapter makes it — "list + detail" is a second
access method, not a reason to defer a source.

- **BambooHR, SmartRecruiters, Rippling** fetch each posting's detail. For BambooHR and
  SmartRecruiters the detail is also the only source of the canonical public URL, and for
  BambooHR the only source of the posted date.
- **BreezyHR is deferred to V1.9**, not because it is expensive but because the description is
  not obtainable at all: `/json/{id}` 302s to `/`, the posting page is a client-rendered shell of
  `%PLACEHOLDER%` text, and `api.breezy.hr/v3` rejects unauthenticated calls. Its list feed alone
  would land two companies' postings as permanently untextable rows.
- A detail response that has drifted (no description where one is expected) **raises**, which the
  pipeline turns into a skipped board with a warning. Landing the posting without its text would
  reintroduce exactly the silent hole this ADR exists to prevent.
- The company blurb that BambooHR, Workable, Rippling and SmartRecruiters bundle next to the job
  text is **not** mapped. It is identical on every posting of a board, so including it would make
  the keyword signals fire on the employer rather than the job.

## Consequences

**Cost is one request per posting**, paid against the per-host rate limit (ADR-0022). It lands
very differently per platform, and this is now a factor in whether a board is worth activating:

- BambooHR's companies each have their own subdomain, so ~155 postings across 32 boards run
  concurrently — the cost is close to free in wall time.
- SmartRecruiters puts every company on one shared host, so a large board is a long serial walk.
  One 871-posting board is ~15 minutes on its own. Adapters stay correct; **which** boards are
  activated is the lever, and a global board whose postings the location gate drops anyway is a
  poor trade.

The alternative — landing list-only rows now and enriching later — was rejected: it puts rows in
silver that quietly bypass every filter, and "we'll backfill descriptions" has no forcing
function once the postings already appear in the digest.
