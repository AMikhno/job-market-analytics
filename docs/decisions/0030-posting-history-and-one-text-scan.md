# 0030 — Posting history is an SCD2 snapshot, and a run reads description history once

**Status:** accepted (revises ADR-0016 on where closed postings outlive raw)

Figures below were measured on 2026-11-09 over 117 days of production landings: 33,437 postings
and 2,043,328 landings.

## Why a snapshot

Raw expires by partition after 180 days, so the oldest landings age out on a fixed date and
`first_seen_at` and closed postings go with them. Keeping raw longer is the expensive way to
retain a small amount of information: those landings hold 54,085 distinct content versions, 2.6%
of the rows.

A window over raw would give the same history for free and would be rebuildable, but it can only
ever see what raw still holds, which is the limit being removed. So `snap_job_postings` is the
one dbt object that cannot be rebuilt from raw. A change to `strip_html` or to an adapter's text
assembly now writes a new version for every affected posting, and that is a cost to weigh before
making such a change.

The snapshot starts empty. It is not backfilled from the 117 days raw still holds, because a
hand-built history would need the snapshot's own metadata columns forged. Raw remains the record
of that period until it expires.

## Check, not timestamp

| | Count | Share |
|---|---:|---:|
| Source timestamp changed, content unchanged | 55,046 of 64,651 timestamp changes | 85% |
| Content changed, source timestamp unchanged | 11,043 of 20,648 content changes | 53% |

Lever exposes only a creation time, so none of its 1,346 content changes moved a timestamp;
Pinpoint publishes none at all. A timestamp strategy would write mostly false versions on some
sources and miss most real ones on others.

`content_hash` alone is not enough either, because it covers title and text. Location changed
1,774 times and remote policy 265 times, so `check_cols` names them, along with `is_active`.

## Closes are a tracked column, not a hard delete

`hard_deletes: invalidate` closes a version when its key leaves the source. The source here keeps
closed postings, so a key leaves only when raw expires, and the snapshot would stamp a close
months after the real one. `is_active` carries real closes instead, and inherits the grace window
that stops a failed board fetch from closing every posting on it. 2,233 postings (6.7%) left a
board on a successful read and later returned; each shows as a close and a reopen.

## What it reads

The snapshot sits upstream of the location filter. Reading `silver_jobs` would make its
population depend on private rules (ADR-0028): widening a rule would look like postings opening
and narrowing it like postings closing. `int_posting_state` projects the tracked columns, because
a snapshot stores every column of its relation in every version and description text would
dominate the table.

One landing can hold several rows for one posting, one per city (193 of 555 Workable postings).
`int_jobs__latest` breaks that tie on location, since an arbitrary choice would record a change on
each run in which a different city won.

## Counting changes honestly

35% of postings had a content change, but 50% of those changes (10,315 of 20,648) arrived in a run
where at least half of a board's postings of five or more changed together, which is what a shared
template edit looks like. Only 17% changed the title. `int_posting_changes` therefore names which
field moved and flags board-wide edits, so the count can separate edits to a posting from edits to
boilerplate.

## One scan of description history per run

Producing `content_hash` and `clean_text` over the retained landings costs about 15 GiB, and each
production run paid it three times: `silver_jobs`, `int_market__tech_mentions`, and a `not_null`
test on the `int_jobs__unioned` view. Those three were 1,225 of about 1,250 GiB billed in the 14
days before this change. `docs/research/ingestion-cost.md` prices storage only, so this did not
appear there.

`int_jobs__latest` materializes the current row per posting once, unfiltered, and everything that
needs current text reads it. The test moved to that table; on the view it could not fail in any
case, since the surrogate key function coalesces nulls.

`int_market__postings` and `agg_market_daily` still read the view. They select neither text nor
hash, so column pruning keeps them cheap.

## Limits

A board-wide edit is an inference from co-occurrence, not an observed template change, and the
thresholds are dbt vars. SmartRecruiters recorded no content change on any of its 3,231 postings
while every other source recorded some, and that is unexplained, so its history should not be read
as evidence that those postings are never edited.
