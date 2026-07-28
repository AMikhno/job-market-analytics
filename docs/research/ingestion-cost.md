# Ingestion cost as sources multiply — a general model

**Status:** proposal, for evaluation next session. Nothing here is built.
**Written:** 2026-07-28, after V1.8 took the pipeline from 3 sources / 123 boards to 9 / 167.

## The problem, stated generally

Ingestion cost — requests, wall time, landed bytes — scales with **everything a board
advertises**. Value scales with **what survives the gates in silver**. Those two quantities are
unrelated, and nothing in the current design connects them.

The design goal is **not** "make the one expensive board cheap". It is:

> Make cost visible per source without being asked, bound it by policy rather than by
> per-adapter special cases, and keep the filtering rules in one place as adapters multiply.

## Measured baseline (2026-07-28, all 167 active boards, one full run)

10,170 postings fetched, 0 boards failed, ~11.5 min wall time.

| Source | Rows | raw MB | description MB | **total MB** | KB/row | % of bytes | → gold | keep rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| greenhouse | 6,576 | 71.8 | 52.7 | **125.3** | 19.1 | **60.0%** | 525 | 8% |
| ashby | 2,012 | 33.5 | 17.4 | **51.2** | 25.4 | **24.5%** | 382 | 19% |
| smartrecruiters | 912 | 10.4 | 4.7 | **15.2** | 16.6 | 7.3% | 8 | 0.9% |
| lever | 303 | 7.3 | 3.3 | **10.6** | 34.9 | 5.1% | 75 | 25% |
| bamboohr | 155 | 1.4 | 1.2 | **2.7** | 17.4 | 1.3% | 119 | **77%** |
| workable | 105 | 1.0 | 0.9 | **2.0** | 18.6 | 0.9% | 44 | 42% |
| pinpoint | 51 | 0.4 | 0.3 | **0.7** | 13.7 | 0.3% | 24 | 47% |
| recruitee | 43 | 0.5 | 0.2 | **0.7** | 16.7 | 0.3% | 0 | 0% |
| rippling | 13 | 0.4 | 0.2 | **0.7** | 51.0 | 0.3% | 2 | 15% |
| **total** | **10,170** | 126.8 | 80.9 | **208.9** | 20.5 | | **1,179** | 11.6% |

**Per run 209 MB → per day 418 MB → steady state 167 GB logical at the current 400-day partition
expiry** (75 GB at 180 days). BigQuery's free allowance is 10 GiB.

Three findings, none of which match where attention was going:

1. **Greenhouse and Ashby are 85% of the bytes.** SmartRecruiters — the board that prompted this
   whole investigation, at ~10 minutes of run time — is 7.3%. The expensive thing was not the
   one anybody suspected, and it had been expensive since long before V1.8.
2. **39% of all bytes are the same text stored twice.** `description_html` (80.9 MB) is
   re-assembled from fields that `raw` also keeps verbatim — Greenhouse's `content`, Ashby's
   `descriptionHtml` (which also ships a `descriptionPlain` copy), SmartRecruiters'
   `jobAd.sections`. This is uniform across sources and will be true of every future adapter
   unless something prevents it.
3. **Keep rate is inversely related to board size.** The local-employer sources V1.8 added keep
   77% / 47% / 42% of what they fetch; the big global boards keep 8–19%, and SmartRecruiters
   0.9%. Coverage of *large* boards is what costs; coverage of *local* ones is nearly free.

## Proposal

Four parts, deliberately separated because they address different costs. **Parts 1 and 3 are
general; part 2 helps a specific (but growing) class of source; part 4 is a policy dial.**

### 1. Per-source cost instrumentation — do this first, regardless of the rest

`ops.ingest_runs` records `rows_fetched` and nothing about cost, so an expensive source is
invisible until a human profiles the pipeline by hand. That is exactly how this document came to
exist, three weeks late, and it found the wrong suspect twice.

Add to `IngestRun` → `ops.ingest_runs`:

| Field | Where it comes from |
|---|---|
| `requests_made` | a counter on `FetchPolicy`/`HostRateLimiter` — already the single choke point for every HTTP call |
| `bytes_landed` | `storage.land` already serializes the rows; return their size |
| `fetch_seconds` | accumulated per board (per-source wall time is no longer contiguous — ADR-0022) |

Then one ops model joins it against what silver kept: **bytes and requests per surviving
posting, per source**. Put the worst offender in the digest footer next to the skipped-board
line that already exists.

This is the part that answers *"who knows what'll appear there"*. You cannot predict which
future ATS is pathological; you can make it announce itself on its first run.

**Watch for:** the counters are written from worker threads — per (source, board) accumulation
must be thread-safe, not a module-level integer.

### 2. Two-phase adapter contract: list → gate → enrich

Generalize `SourceAdapter` from one method to two:

```python
class SourceAdapter(Protocol):
    def list_candidates(self, session, board_ref) -> list[Candidate]: ...
    def enrich(self, session, candidate) -> RawPosting: ...
```

The **pipeline**, not the adapter, decides whether to enrich, by applying a shared seed-driven
predicate between the phases. A new ATS implements two well-defined steps and inherits whatever
cost policy exists at the time; the policy can change without reopening nine adapters.

**Be honest about the ceiling: this saves requests and wall time, not bytes.** It only bites
where a source pays per posting — today BambooHR, SmartRecruiters, Rippling, i.e. 8.9% of bytes
but a large share of run time. Greenhouse and Ashby return the whole board in one response;
there is no per-posting call to skip, so the gate cannot touch the 85%.

It is still worth considering, because **detail-fetching sources are likely to be the norm going
forward** (of the six ATS added in V1.8, three needed per-posting calls), and run time is what
makes a schedule fragile.

**Watch for:** pagination and Rippling's collapse-by-uuid both belong in phase 1, before the gate
sees candidates. `description_html` stays non-optional — a skipped enrichment lands empty text
only for postings the gate already judged out, so "everything reaching gold has a description"
still holds.

### 3. One gate definition, two engines — with a test that they agree

The predicate reads the **same dbt seed** silver reads (`dbt/seeds/allowed_locations.csv`), so
"filter rules are data" (CLAUDE.md) still holds: the rule is not reimplemented in Python, it is
*read* by Python.

- Silver stays **authoritative** — it re-applies the full rule to everything landed.
- Python's copy is **advisory and conservative**: skip enrichment only when a candidate is
  *definitively* disqualified (non-null location matching nothing). Null/unknown always
  enriches. Being wrong costs a request, never a posting.
- It **self-heals**: raw re-lands the whole board every run (`WRITE_APPEND`), so widening the
  seed means the next run fetches full text for everything skipped before. Full ingestion-side
  filtering — dropping rows outright — would destroy this property, and would also make "this
  source landed 0 rows" ambiguous between *board is dead* and *nothing local this week*.

**The drift risk is the real price, and it grows with every adapter.** Mitigation: a golden
fixture set of location strings asserted to produce identical verdicts from the Python predicate
and from `regexp_word_ci` in dbt, failing the build on disagreement.

**Open question:** is location the only pre-enrichment dimension, or is this a general
"pre-filter" hook (title patterns next)? More dimensions = larger blast radius when the two
engines disagree.

### 4. Payload discipline and storage policy — the biggest lever, and the most general

Ordered by measured impact:

- **Stop storing the same text twice — worth ~39% of all bytes, every source, forever.**
  `raw` should keep only what the mapping did *not* already extract. Needs a shared helper
  (drop mapped keys) plus a test that fails when description text appears in both places, or
  every future adapter re-introduces it. This is the change that best fits "who knows what'll
  appear there": it is a property of the schema, not of any one ATS.
- **Retention is one knob**: `bq_raw_partition_expiry_days`, 400 → 180 halves the footprint
  (167 GB → 75 GB) and was already the stated preference.
- **Compression is one setting**: `ALTER SCHEMA jobs_raw SET OPTIONS(storage_billing_model =
  'PHYSICAL')`. JSON of this shape typically compresses 5–10×. Caveat: physical billing also
  charges time-travel (7d) and fail-safe (7d) bytes, so measure rather than assume.
- Combined, those three take a 167 GB steady state to roughly **5–10 GB** — back inside the free
  allowance — with no change to what the pipeline fetches or keeps.
- **GCS + Parquet + BigQuery external tables** (the S3-equivalent archive tier) stays *out* of
  scope as a cost fix, with a stated trigger instead: **steady-state logical raw > 50 GB after
  the three levers above, or a real analytical need for >6 months of history.** Below that, the
  export job, Hive-partitioned layout, external table and lifecycle rules cost more attention
  than they save. Above it, the shape is `EXPORT DATA … format='PARQUET'` per expiring partition
  → `gs://…/raw/{source}/dt=…/` → external table → GCS lifecycle Standard → Coldline.

## Sequencing, if adopted

1. **Instrumentation** (part 1) — changes no behavior, produces the numbers that decide the rest.
2. **Payload discipline + retention + compression** (part 4) — ~39% + 55% + 5–10× against a
   problem that is 85% concentrated in two sources the other parts cannot help.
3. **Two-phase contract** (part 2) with `enrich` as a no-op everywhere — a pure refactor,
   provable by the existing adapter tests.
4. **The gate** (part 3) — only once part 1 shows which sources it would help and by how much.

## The case against parts 2 and 3

Stated plainly, because it may be right: **curating which boards are active is a lever that
already exists and costs nothing.** One row turns a bad board off, and the measurement shows the
worst offenders are few and obvious once someone looks.

The counter-argument is that curation is a human noticing, per board, forever — which is what
part 1 fixes. If part 1 lands and the digest names the worst source every run, parts 2 and 3 may
never be needed.

## Related finding, not about cost

Of **1,179 gold postings across all 167 boards, 40 are title-matched** (38 of those also hit a
desired technology). Every one comes from Greenhouse, Ashby or Lever — the six sources added in
V1.8 contributed **316 gold postings and 0 title matches**. That is the answer to the open
question in `TODO.md`: after doubling coverage twice, the constraint is relevance, not sources.
V2 scoring, not V1.10 adapters.
