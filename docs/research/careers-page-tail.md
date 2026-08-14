# The 334 companies with a careers page and no ATS

**Status:** measured findings, 2026-08-02. Nothing built. Proposes a *change signal*, not a source.

`config/discovery/ats_audit_results.csv` (873 companies audited) splits three ways:

| Bucket | Companies |
|---|---:|
| **"Unknown/Custom" — rendered, no ATS detected** | **334** |
| No career page at all | 251 |
| On a recognized ATS | 288 |

The 334 are the open question: is there a way to ingest them without one scraper per company?

## Probe: 40 random custom careers pages, 2026-08-02

| Shape | Count |
|---|---:|
| `schema.org/JobPosting` JSON-LD | **0** |
| Parseable list of job links (≥3 title-shaped anchors) | **2** |
| Prose + "email us your resume" | 11 |
| Real text, nothing job-shaped | 23 |
| 403 / unreachable | 4 |

**JSON-LD is absent on this tier.** That was the obvious general solution — Google for Jobs
requires `JobPosting` structured data and mandates a `description`, so one generic parser would
have covered every site that has it *and* cleared ADR-0021 by construction. It is simply not
present here. Sites with an ATS emit it; sites without one do not.

**They are not, mostly, JavaScript-rendered.** Re-probing 12 of the "nothing job-shaped" pages:
8 served real text, 3 were app shells, 1 was thin. So BeautifulSoup can *see* these pages — a
plain `requests.get` is enough for most of them.

## The finding that decides it: there is usually nothing to parse

Only **2 of 40** had a structured list of openings. The dominant shape is a careers page with
plenty of real prose and **no machine-readable openings at all** — "we're always looking for
talented people, send your resume to careers@…".

No parser fixes that. BeautifulSoup, JSON-LD and an LLM all extract the same zero postings from a
page that lists zero postings. **"Is BeautifulSoup sufficient?" is the wrong question**: parsing is
not the bottleneck, content is. Roughly 5% of this tier has anything to extract.

Two further reasons not to build extraction here:

- **Silent decay.** A heuristic HTML parser returning 0 postings after a site redesign is
  indistinguishable from "this company has no openings." That is the exact failure mode the
  pipeline is otherwise engineered against (strict adapter parsing, freshness gates, the
  `unconfigured` warning) — and the one that cost four days in the 2026-07-28 outage.
- **Yield is anti-correlated with the target.** Having a real ATS is itself a signal of a real
  hiring function, which correlates with a real data function. This tier skews to construction,
  consultancies and hardware — the segment that produced zero data roles in
  `relevance-signals.md`. The companies actually wanted (mid-size SaaS that runs on data) are
  essentially always on Greenhouse, Lever or Ashby.

## The counter-argument, which is real

A posting that exists only on a company's own HTML page is **genuinely lower-competition** than a
Greenhouse req syndicated to Google Jobs, LinkedIn and Indeed. For a local search that is real
edge — the same logic `relevance-signals.md` already applies to postings over 90 days old
("email the team, don't apply cold").

It argues for a different artifact, not for extraction. **If the application process is "email us",
you do not need to detect a posting in order to act.** You need to know the company exists, that
it is the right shape, and ideally that something changed recently.

## Proposed instead: a change signal (~50 lines, not an adapter)

1. **Hash the careers page's visible text on a schedule** (monthly is enough — this tier does not
   move fast). Store `(company, url, sha256, last_changed_at)`.
2. **When the hash changes, surface the company** in the digest as "worth a look" — never as a
   posting.
3. *Optionally*, **LLM-extract only on change.** Heterogeneity is exactly what an LLM handles and
   a deterministic parser cannot. If ~20 of 334 pages change per month, that is ~20 calls.
   Guard against hallucination by requiring every extracted posting's URL to appear among the
   page's actual links.

Why this shape is right:

- Works identically on prose, on lists, and on "email us" pages.
- **A redesign registers as "changed", which is correct** — it cannot silently report zero.
- Produces a *companies-to-check* feed, a separate digest section, not a new `raw_*` source. No
  dedup, no lifecycle, no freshness gate, no ADR-0021 problem.
- Normalization matters: strip scripts/styles, collapse whitespace, and ignore obvious churn
  (CSRF tokens, timestamps, cache-busting query strings) or every page changes every run.

## Priority

Below V2 and below adding companies on already-built ATS. Cheap enough to be worth doing
eventually, which "334 scrapers" never was.

Probe scripts: `tools/probes/careers-tail/`.
