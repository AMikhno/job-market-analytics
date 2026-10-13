# Probe — embedding search as a company-discovery source

Decides one thing before any tool gets built: **does searching ATS posting hosts surface boards
the company list does not already have?** Everything else about the idea is downstream of that
number.

## Why posting URLs rather than company websites

`tools/company_discovery/ats_audit.py` asks *"what does this company's website call?"* — which
fails exactly when the site does not say, and which needs a candidate company list as input.
This asks the inverse: *"which board mentions this kind of work?"* A result URL like
`.../<ref>/jobs/<id>` carries the ATS **and** the `board_ref`, so a single hit yields a candidate
with no render, no token guessing, and no `.xlsx` assembled by hand.

**Index staleness is not a threat here.** A posting crawled six months ago still names a board
that gets fetched live every day afterwards — the posting is a pointer, not cargo. A dead *board*
is a different problem, and the liveness check catches it: a candidate is only proposed if the
keyless V1 API returns records > 0 today (an empty board is indistinguishable from a wrong one).

## Running it

```bash
export EXA_API_KEY=...                       # local env only, never a CI secret
uv run python tools/probes/exa-discovery/probe_exa_queries.py --dry-run   # spends nothing
uv run python tools/probes/exa-discovery/probe_exa_queries.py
```

**$0.022 per query at the default 25 results** — $7/1k requests covers the first ten results and
each one beyond that adds $1/1k, so a query is not the headline rate. The dry run prints the plan
and its estimate without spending; the figure printed at the end of a real run is what the API
itself reported.

`--max-queries` is a hard spend cap. It slices in variant order, so lowering it drops whole
trailing arms of the experiment rather than sampling across them; the script warns when that
happens. `--only-source` and `--bare-domains` diagnose a source that returned nothing, and write
to their own output file so a three-query check cannot overwrite a full run's results.

## The query file

Queries live in `config/discovery/exa_queries.json`, which is **gitignored** — desired titles and
tech are preferences, and a preference is private config (ADR-0028). Structure:

```json
{
  "variants": {
    "A-title":          ["Senior Backend Engineer job posting"],
    "B-stack":          ["job posting requiring Kubernetes, Go and Terraform"],
    "C-responsibility": ["role owning the deployment platform and its reliability targets"],
    "D-geo":            ["Backend Engineer job in Ottawa Ontario Canada using Go"],
    "E-profile":        ["mid-size B2B SaaS company hiring for its platform team"]
  }
}
```

Phrase queries as **descriptions of the target page**, not keyword strings — that is what an
embedding index matches against.

The variants are the experiment, not decoration. `docs/research/relevance-signals.md` found that
requirements text beats titles 3/13 against 1/21, because every corporate job mentions dashboards
and only a data job requires dbt. If that carries into *retrieval*, **B and C should beat A** on
verified-new-boards per query. If A wins instead, the embedding is capturing something the keyword
intuition does not, and the eventual query file should be built on titles.

## The geography tier

Posting-level geography is **not** the problem here — nothing on these boards is being ingested,
and `allowed_locations` filters postings downstream as usual. The problem is a company that will
never produce a takeable posting: adding it means fetching its board daily forever for zero gold
rows. So the gate is applied to the *company*, at verification, using postings the liveness check
already downloaded — free, and consistent with silver by construction because it reads the same
seed.

The company gate is deliberately **broader** than the posting gate:

| Tier | Rule | Why |
|---|---|---|
| `in-range` | ≥1 posting matches `allowed_locations` | fetchable today |
| `watch` | no match, but no exclusion term either | **kept** — a board can open a posting inside the allowed set later, and silver filters the rest regardless |
| `excluded` | ≥80% of located postings match an exclusion term | fetching it daily returns nothing for any rule |

The exclusion terms live in `config/discovery/non_viable_locations.txt`, gitignored for the reason
`dbt/seeds/*.csv` is: a filter rule is a preference (ADR-0028). With no such file the `excluded`
tier is never assigned and every live board lands in `watch`.

Keep that list **narrow**. An omitted term costs one extra board to review; an over-broad one
silently deletes a company that nothing will mention again. Excluded rows stay in the output file
for the same reason, with their `top_locations` as the evidence for overruling the tier —
under-filter and show the evidence, because a silent drop is not a reviewable decision.

## Reading the output

Per variant and overall:

| Metric | Means | Good |
|---|---|---|
| extraction rate | results whose URL yields a valid `board_ref` | high — else the host list is wrong |
| **novelty** | extracted refs not already in master or audit cache | **the decision metric** |
| liveness | new refs whose keyless API returns postings today | high — else refs are stale or junk |
| in-range | verified boards with a posting in an allowed location | the strict read |
| keep/qry | in-range + watch per search | what a query variant is worth |

**Pre-registered gate:** build the tool only if **novelty ≥ 30%** and **≥ 1 in 3** verified boards
land `in-range`. Below that, the free credits answered the question for nothing — which is the good
outcome, not a failed probe.

Candidates land in `config/discovery/exa_pilot_candidates.csv`. **Eyeball it before believing it.**
Note that the portal/directory failure mode documented in `tools/company_discovery/README.md`
cannot occur here by construction — discovery starts *from* a board rather than from a website
that might embed someone else's — but a staffing agency's board is real, is a deliberate target
(TODO.md priority 2), and still needs a human to say so.

## Measured results

The run, its cost model, and the decisions it produced are in
[exa-discovery](../../../docs/research/exa-discovery.md). Read it before changing the query file.

## Reviewing the output

`probe_label_analysis.py` takes a TSV or spreadsheet CSV of `url, verdict, why` and joins it to the
roles file, reporting rates per title rule, per query variant, per source and per board. Every
grouping is derived from the data, so changing the queries or the title seed needs no change here.

Two mechanics worth knowing:

- **Match on the posting id, not the URL.** Several ATS redirect to a company-hosted slug rather
  than the `absolute_url` the API returned, so URL-equality silently under-joins.
- **A verdict that is neither yes/maybe/no is kept as `other`**, never folded into `no` — a review
  can reject a posting while endorsing the board, and the board is the unit being merged.

## What it does not answer

Whether those companies produce relevant postings **over time**. One review pass covers one day's
openings; the company-level claim is tested by the digest over months.
