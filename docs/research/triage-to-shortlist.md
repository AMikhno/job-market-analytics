# How 1,305 gold postings became 75 — the manual pass V2 has to reproduce

**Status:** measured record of a one-off manual triage, 2026-07-28. Not a decision.
**Why it exists:** V2 scoring (ADR-0020, `docs/v2-plan.md`) is meant to automate this pass. The
pass was done once by hand, end to end, and it produced a set of findings about *what actually
decides fit* that are not derivable from the pipeline's current signals. Those findings are the
input contract for the V2 prompt. Without them V2 will rebuild the same keyword matcher that
this exercise proved insufficient.

Source data: local DuckDB gold build, all 168 boards, 2026-07-28. Prod held 981 rows at the time
and was still running `main`'s dbt (no `match_score`), so local was the better dataset.

## The funnel

| Stage | Count | What removed the rest |
|---|---:|---|
| Fetched from 168 boards | 10,170 | — |
| Gold (location gate + lifecycle) | 1,305 | the only *hard* rule: `allowed_locations` |
| `title_match = true` | 40 | seed pattern on the title |
| **Judged worth applying to** | **75** | manual read of title + description |
| Of those, actionable now | 44 | ≤30 days old, eligible, within the level band |
| Apply immediately | 15 | the above + top-25 content fit |

**40 and 75 barely overlap, and that is the whole point.** `title_match` is not a subset of the
shortlist and the shortlist is not a superset of it. Both directions failed:

- **Best-titled posting in the file fell 15 ranks** once its description was read. Perfect title,
  densest keyword match, and the actual work was month-end financial close.
- **Six genuinely strong roles were sitting in the reject pile**, filed under titles that name the
  org chart rather than the work: *Forward Deployed Engineer*, *Salesforce Integration Developer*,
  *AI Solutions Engineer*, *Lead Data Scientist*, *Senior People Data Scientist*, *ERP Specialist*.

The generalization, which is the single most useful sentence here:

> **Companies name roles after the org chart the seat sits in, not the work being done.**

A posting for analytics work sitting under an engineering VP gets an engineering title; the same
work in a field organization gets a customer-facing one. It is why title can never be the key, and
why ADR-0015 made the title seed a soft signal.

## What the manual pass actually ranked on

In priority order. None of these are in the pipeline today except crudely.

1. **Named-tool content in the *requirements*** — SQL, dbt, Snowflake, Tableau, Airflow,
   Salesforce. Not mentions anywhere on the page; requirements specifically (see below).
2. **Work eligibility**, against the profile's stated region. This is a *hard* gate on acting, but
   it must not be a hard gate on *ingesting* — see the location trap below.
3. **Seniority fit against the profile's level band.** Both directions matter: too junior is a
   reject, and so is a role above the band. See the "Manager" trap below.

Note that 2 and 3 are read from `config/profile.yaml` (gitignored, ADR-0020) — the values belong
to the searcher, not to this document. What is recorded here is only that these are *dimensions
the prompt must handle*, and how each one fails.
4. **Liveness.** A tiebreaker, never the sort key — a 34-day-old analytics lead outranks a
   1-day-old backend req.

## Four traps, each measured

**1. Whole-page keyword scoring is ~5% precise; requirements-only scoring is ~23%.**
A keyword pass over the 1,077 title-only rejections flagged 21 as analytics-dense. **20 were false
positives** — Finance VPs, L&D managers, controllers, a Head of Procurement. Every corporate job
now says "dashboards" and "stakeholders" somewhere. Re-scoring **only the requirements section**
cut 21 flags to 13, of which **3 were real misses**. All three made the final 75.

> **V2 instruction:** score the requirements/qualifications section, not the whole posting. A
> Finance Manager *describes* reporting in its responsibilities blurb; it never *requires* dbt.

**2. Bare `Remote` hides country restrictions.** The location gate keeps unqualified `Remote`, so
41 US-only postings from a single US insurer reached gold with perfect analytics titles. They are
correctly ingested and correctly *not* applied to.

> **V2 instruction:** eligibility is a distinct output field, not a filter. Emit
> `eligibility ∈ {ca_ok, us_only, unclear}` from the description text and let it rank, never
> delete — the pipeline cannot tell "Remote" from "Remote (US)" and the description usually can.

**3. "Manager" in an analytics org is often an IC band.** The top-ranked posting in the final list
was titled *Revenue Analytics Manager* and its description listed no reports and no hiring — the
deliverables were analysis, datasets and recommendations. Meanwhile five roles were cut *because*
they managed people, including one whose tool stack was a perfect match two levels up.

> **V2 instruction:** infer level from the described work (reports? hiring? headcount? scope
> statements?), not from the title, and emit a `level_fit` with a `verify` state rather than a
> binary. Titles are unreliable in both directions here.

**4. Deal-breaker tech is a demotion, not a delete.** Across the 1,305, **123 postings name a
deal-breaker technology** — Spark 88, Kafka 48, Scala 17, Flink 16, Hadoop 8, i.e. ~9% of the
market. Enough of them were otherwise strong that deleting would have cost real matches. This is
already encoded as ADR-0023; the measurement is here so nobody re-litigates it.

## Output shape the pass produced

Per posting: `rank`, `why` (1–2 sentences from the description), `priority`, `seniority`,
`level_fit`, `eligibility`, `cv_version`. Priorities used:

| priority | meaning |
|---|---|
| `APPLY NOW` | ≤30d, eligible, at level, top-25 content fit |
| `apply this week` | same gates, ranked 26–75 |
| `confirm still open` | 31–90 days old |
| `verify eligibility first` | strong fit, US-remote wording |
| `stale - contact directly` | >90 days — email the team, do not apply cold |
| `below level - only if negotiable` | junior-titled |

**On "live":** gold already drops postings that vanish from their board or whose board stops
responding for 36h, so every row is live in the strict sense. What no pipeline can see is whether
a posting is *effectively* filled — published with an offer already out. That is why anything
past 30 days gets "confirm" rather than an apply instruction.

## Market-shape findings (context for list building, not for scoring)

- **Only 5 of 1,305 postings were titled "Analytics Engineer" — and all 5 were shortlisted.**
  Conversion 5/5, versus 31/43 for "Analyst" titles. The scarcity is supply, not filtering.
- **73 of the 113 companies with a live posting produced zero data-shaped roles.** Ten companies
  produced 49 of the 75.
- **Companies that *sell* data tooling have almost no analytics jobs; companies that *run on*
  data have plenty.** Measured: three large data-platform vendors contributed 1,639 postings and
  **zero** shortlisted roles, their analytics vocabulary living in Finance and Sales Ops instead.
  The best conversion in the file was a mid-size health-tech company: 11 postings, 4 shortlisted.

That last line is the list-building rule, and it supersedes "add big tech companies."
