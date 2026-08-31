# What actually predicts relevance — measured against a manual pass

**Status:** observations from a one-off **LLM** pass over a full gold build. Not a decision, and
**not measurements** — see the provenance note below before relying on any number here.

V2 scoring (ADR-0020, `docs/v2-plan.md`) automates a judgement that was made once by hand, over
every posting in gold. Doing it manually produced measurements about *which signals decide
relevance* that the pipeline's current keyword rules cannot express. Those measurements are the
input contract for the V2 prompt: without them, V2 rebuilds the keyword matcher this exercise
disproved.

Source: a full local gold build across all active boards — 10,170 postings fetched, 1,179
surviving to gold.

**Partially re-measured 2026-08-31** against the prod warehouse (1,742 silver survivors, 1,293
gold) — see [Re-measured](#re-measured-2026-08-31) at the end. Only the counting claims could be
re-run; every precision claim below still rests on the single LLM pass described next.

> **Provenance, and why it limits what this file can claim.** The pass was a single LLM run, not a
> human review. It was given a resume and asked to return the ~75–80 best postings by *work* match
> regardless of title, ordered by recency. So it produced a ranked shortlist, not a judgement on
> every gold row, and the counts below describe what one model selected rather than what is
> actually relevant.
>
> Two consequences. **These numbers cannot serve as an evaluation set for V2** — grading an LLM
> scorer against LLM-generated labels measures agreement with a predecessor, including its
> mistakes, so the eval set has to be human-labelled. And the run predates the resume corpus, so it
> matched against a single tailored resume version; anything that version omitted was invisible to
> it. Re-measure once the corpus exists.
>
> What survives regardless is the *shape* of the argument: the pass was already doing
> resume-against-requirements matching, ad hoc and in one prompt. V2 formalizes what it did rather
> than inventing a new approach.

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

## Four instructions the V2 prompt has to encode

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

## Re-measured 2026-08-31

Against the prod warehouse rather than the original local build: **1,742 silver survivors, 1,293
gold**, roughly half again as large as the set above. Only the claims that are pure counting are
re-run here. Every precision claim in this file — 1/21 against 3/13, 5/5, 31/43 — needs human
labels by construction, per the provenance note, and none exist yet.

Two conditions on reading these numbers. The corpus is a **2026-08-16 snapshot**: ingestion had
stalled and the figures do not describe the market on the date in the heading. And the fit scores
were produced against a *tailored* resume rather than the full corpus, so their distribution
measures the tailoring as much as the market.

Also excluded: anything computed from the seeds. The warehouse was running an older, much smaller
rule set than the one now in place, so `title_match`, `match_score` and `deal_breaker_hits` were
not comparable to anything and are omitted. What follows is seed-independent — measured against
posting text, titles and the V2 extraction fields directly.

**The deal-breaker rate holds.** Measured against posting text: **164 of 1,742 postings (9.4%)**
name one of the five terms the original pass counted, against 123 of 1,179 (~9%) before. Per term:
Spark 108, Kafka 73, Scala 22, Flink 23, Hadoop 12. The corpus grew by half and the proportion
moved by 0.4 points, so "roughly 9% of the market" is a property of the market, not of one
snapshot. ADR-0023 rests on this and does not need revisiting.

**Scarcity holds.** Titles word-matching "Analytics Engineer": **6**, up from 5, out of 1,742 —
0.3% of the corpus. Titles matching "Analyst": 60, up from 43. The supply argument stands: no
amount of better scoring produces more of them.

**Concentration holds.** **87 of 119 companies** with a live posting produced no title-matched
role at all, against 73 of 113 before — the same roughly-three-quarters shape.

### What the original pass could not measure

The V2 extraction fields did not exist when this file was written. Three of them now quantify
claims that were previously arguments.

**Instruction 3 is now a measurement, and it is the strongest result here.** Of postings *titled*
Manager, Lead or Head, **207 describe no people management against 135 that do**, with 20 unclear
— so **58% of manager-titled postings are individual-contributor work**. The error runs the other
way too: **124 of 931 postings (13%) not titled that way do manage people**. A title-based rule for
seniority would therefore be wrong more often than right on the postings it fired on, which is the
org-chart-naming claim at the top of this file, now with a number under it.

**Instruction 2's predicted leak is real and sized.** Of 1,293 gold postings the location gate
admitted, **160 (12%) are `us_only`** on the description, and **423 (33%) are `unclear`**. The
rule-based gate cannot see either, which is what the instruction argued; a third of gold being
`unclear` also says the field is doing honest work rather than guessing.

**The fit-score distribution is bimodal, and unexplained.** 722 postings score 1, 103 score 2, 399
score 3, 41 score 4, 28 score 5. The trough at 2 and the pile at 1 and 3 is the shape a rubric
produces when it is really answering a three-way question, not a five-way one. Only 5% score 4 or
5. Whether that is the market being genuinely thin or the scorer compressing its range is exactly
what human labels decide — do not tune the rubric against this shape before they exist.
