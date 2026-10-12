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
uv run python tools/probes/exa-discovery/probe_exa_queries.py             # ~$0.13
```

Roughly $0.007 per search; the 30-query plan costs about **$0.21**, and a new account's signup
credit covers it many times over. `--max-queries` is a hard spend cap — it slices in variant
order, so lowering it drops whole trailing arms of the experiment rather than sampling across
them; the script warns when that happens. The spend printed at the end is what Exa itself
reported, not an estimate.

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
| `in-range` | ≥1 posting matches `allowed_locations` | takeable today |
| `watch` | no match, but not clearly non-viable (US, global, unknown) | **kept** — a US company opens Canadian and remote reqs later, and silver filters its postings anyway |
| `excluded` | ≥80% of located postings are somewhere that will not hire from here | salary band and timezone, not capability |

`excluded` covers India, China, the APAC offshore hubs and continental Europe. **UK, Ireland,
Israel and Australia are deliberately not on that list** — they land in `watch` with their
locations printed, rather than being dropped on a guess. Excluded rows stay in the output file
too: the tier is a heuristic over location strings, and the `top_locations` column is the evidence
for overruling it. Under-filter and show the evidence; a silent drop is not a reviewable decision.

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

## What it does not answer

Whether those companies produce postings **worth applying to**. That needs the pipeline to run
with them in the list, and then the labelling work in TODO.md's V2 validation. Novelty is a
necessary condition, not a sufficient one.
