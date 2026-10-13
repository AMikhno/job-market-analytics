# Search-led company discovery

**Status:** measured 2026-09-01. Probes in `tools/probes/exa-discovery/`.
Proposes a **company** source, not a posting source.

`ats_audit.py` answers "what ATS does this company use?" — it needs a candidate list as input and
goes quiet when a site never names its board. This asks the inverse: search the ATS *posting* hosts
directly. A result URL shaped `.../<ref>/jobs/<id>` carries the ATS and the `board_ref` together, so
a hit is a candidate company with no render and no token guessing.

**Index staleness does not threaten this.** A posting crawled months ago still names a board that
gets fetched live every day afterwards — the posting is a pointer, not cargo. A dead *board* is
caught by verifying against the keyless API before anything is proposed.

## Funnel, 32 queries

| Stage | Result |
|---|---:|
| Results returned | 800 |
| → parsed to a valid `board_ref` | **798 (100%)** |
| → not already in the list or the audit cache | 748 (94%) |
| → board answers live, with postings | 395 (53%) |
| → at least one posting in an allowed location | 134 (34%) |
| → **companies** also carrying a title the seed wants | **61** |

Thirty of the 61 carried exactly one matching posting.

**Merged 2026-10-13: 60 boards, 58 of them active.** One candidate was dropped on review as an
aggregator — a board whose posting count is an order of magnitude past any single employer's, which
is the same portal signature `tools/company_discovery/README.md` documents arriving by a different
route. Two more merged inactive: one internal-applicants-only board, and one that answered on
discovery but not on the re-check a human ran before merging, which is the reason that re-check
exists. `merge_companies.py` reported **0 conflicts and 0 fields filled**, confirming the funnel's
de-duplication: every board was new to the list, and none of them overlapped a row it already held.
The run grew the active list by roughly a third.

**Cost: $0.704**, plus $0.096 on the source diagnostic below. Billing is $7/1k requests for the
first ten results and $1/1k for each result beyond ten, so `numResults=25` costs $0.022/query —
costing a query at the headline rate alone under-estimates a run by 3.2x.

## The unit of merge is the company

What enters `config/companies.csv` is a board, and the pipeline fetches that board every day
thereafter. The posting that led there is a pointer: it may be closed before the board is ever
fetched, and a board carrying nothing relevant today can carry something next month. So a board
qualifies on being live, reachable and in range — not on the posting that surfaced it.

This is also why the location gate runs at company level and is deliberately **broader** than the
posting gate. A posting outside an allowed location costs nothing downstream, because silver filters
it. A company that can never post one is fetched daily forever for no gold rows. Boards tier
`in-range` / `watch` / `excluded`, and `watch` is kept.

## Title patterns removed: pre-sales and agent-engineering collisions

Several title patterns in `desired_titles` match postings for a different occupation than the title
names, and were removed from both the seed and the query file:

- **Solutions Architect, Solutions Engineer, Analytics Consultant** denote pre-sales and integration
  work in this market. Observed instances were marketing-technology, insurance, customer-identity
  and network-engineering postings.
- **Forward Deployed** has shifted to denote LLM and agent application engineering.

Both groups retrieved postings outside the seat the seed exists to match, at volume: they accounted
for roughly a quarter of everything a hand-review pass covered and produced no usable match. This
is a property of how the titles are used today, so it is worth re-checking rather than treating as
settled — the same drift that captured these terms can release them.

**`Data Engineer` was kept** despite a low rate for the opposite reason: the same posting text
frequently describes an analytics seat under that title, so the pattern earns its place as a catch
even when most of what it catches is discarded.

## Source coverage limit

**The index holds very few BambooHR company boards**, and that is a real coverage gap rather than a
filter bug. Tested both ways, 2026-09-01:

| Domain filter | Results | Extraction |
|---|---:|---:|
| `*.bamboohr.com` | 13, 14, 12 against a requested 25 | 100% |
| `bamboohr.com` | 25, 25, 25 | 32% |

The wildcard form **exhausted the index**, which only happens when the filter matches; the bare form
returned a full page at a third the extraction rate, the remainder being the vendor's own marketing
pages. So the wildcard is both working and strictly better. Recruitee (7 boards) and Pinpoint (1)
are plausibly the same limit, inferred rather than measured.

## Reading the output

Two traps, both of which produced wrong conclusions on the first pass:

- **The roles file is sorted by source.** Reading the first N rows samples one ATS, not the run.
- **A labelled URL is whatever the browser settled on.** Several ATS redirect to a company-hosted
  slug rather than the `absolute_url` the API returned, so URL-equality under-joins; match on the
  posting id, which survives the rewrite.

## What this does not answer

Whether the discovered companies produce relevant postings *over time*. A single review pass covers
one day's openings. The company-level premise — that a board with nothing relevant today is still
worth fetching — is tested by the digest over months, not by this run.
