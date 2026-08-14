# What actually predicts relevance — measured against a manual pass

**Status:** measured findings from a one-off manual review of a full gold build. Not a decision.

V2 scoring (ADR-0020, `docs/v2-plan.md`) automates a judgement that was made once by hand, over
every posting in gold. Doing it manually produced measurements about *which signals decide
relevance* that the pipeline's current keyword rules cannot express. Those measurements are the
input contract for the V2 prompt: without them, V2 rebuilds the keyword matcher this exercise
disproved.

Source: a full local gold build across all active boards — 10,170 postings fetched, 1,179
surviving to gold. Every gold row was reviewed by title, and a large subset also by description.

## Title matching fails in both directions

This is the central result, and it is symmetric — which is what makes it interesting.

- **The best-titled posting in the set was not relevant.** Perfect title match, densest keyword
  hit count in the file, and the described work turned out to be month-end financial close.
- **Six genuinely relevant postings sat in the reject pile**, under titles naming the org chart
  rather than the work: *Forward Deployed Engineer*, *Salesforce Integration Developer*, *AI
  Solutions Engineer*, *Lead Data Scientist*, *Senior People Data Scientist*, *ERP Specialist*.

The generalization:

> **Companies name a role after the org chart the seat sits in, not the work being done.**

Analytics work under an engineering VP gets an engineering title; the same work in a field
organization gets a customer-facing one. This is why title cannot be the key, and why ADR-0015
made the title seed a soft signal rather than a filter.

## Four measurements the V2 prompt has to encode

**1. Score the requirements section, not the whole posting.**
A keyword pass over the title-only rejections flagged 21 as analytics-dense. **20 were false
positives** — finance managers, L&D managers, controllers, a head of procurement. Every corporate
job now says "dashboards" and "stakeholders" somewhere. Re-scoring **only the requirements
section** cut 21 flags to 13, of which **3 were real** — 1/21 precision against 3/13.

> A responsibilities blurb *describes* reporting. Only a data role *requires* dbt.

**2. Eligibility is an output field, not a filter.**
The location gate keeps unqualified "Remote", so postings restricted to another country reach gold
with entirely relevant titles — correctly ingested, and correctly not applied to. The pipeline
cannot tell "Remote" from "Remote (US)"; a description usually can.

> Emit `eligibility ∈ {ok, restricted, unclear}` from the description and let it rank. Never
> delete on it.

**3. Level must be inferred from the described work, in both directions.**
The highest-ranked posting in the final set was titled *Manager* and its description listed no
reports, no hiring, and deliverables that were entirely individual. Meanwhile five postings were
rejected precisely *because* they managed people, including one whose tool stack was otherwise an
exact match.

> Infer level from scope statements — reports? hiring? headcount? — not from the title, and emit
> `level_fit` with a `verify` state rather than a binary.

**4. Deal-breaker technologies demote, they do not delete.**
Across the gold set, **123 postings named a deal-breaker technology** — Spark 88, Kafka 48, Scala
17, Flink 16, Hadoop 8, roughly 9% of the market. Enough were otherwise strong that deleting them
would have cost real matches. Already encoded as ADR-0023; the measurement is here so it is not
re-litigated.

## What the pipeline cannot see

Gold drops postings that vanish from their board, or whose board stops responding, so every row is
live in the strict sense. What no pipeline can determine is whether a posting is *effectively*
filled — still published with an offer already out. Age is the only available proxy, which is why
older postings warrant a different action rather than a lower score.

## Market shape — for list building, not for scoring

- **Only 5 postings in the entire set carried the title "Analytics Engineer", and every one was
  relevant** — 5/5, against 31/43 for postings titled "Analyst". The scarcity is supply, not
  filtering, so no amount of better scoring produces more of them.
- **73 of the 113 companies with a live posting produced zero data-shaped roles of any kind.** Ten
  companies produced two-thirds of everything relevant.
- **Companies that *sell* data tooling have almost no analytics jobs; companies that *run on* data
  have plenty.** Three large data-platform vendors contributed 1,639 postings and zero relevant
  ones — their analytics vocabulary lives in Finance and Sales Ops. The best conversion in the set
  was a mid-size company with 11 postings, 4 of them relevant.

That last point is the list-building rule, and it replaces "add large well-known companies".
