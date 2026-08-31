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

**Re-measured 2026-08-31** against the prod warehouse — 2,137 silver survivors, 1,361 gold, under
the deployed rules and the full resume corpus. See [Re-measured](#re-measured-2026-08-31) at the
end: the counting claims were re-run and hold, and three of them now have a third independent
sample. Every precision claim below still rests on the single LLM pass described next.

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

**2,137 silver survivors, 1,361 gold, 2,276 scored** — the first measurement where the rules, the
corpus and the postings are all current. Everything below is counting. Every precision claim above
— 1/21 against 3/13, 5/5, 31/43 — still needs human labels by construction, per the provenance
note, and none exist yet.

**The deal-breaker rate holds a third time.** The five terms the original pass counted appear in
**222 of 2,137 postings (10.4%)**, against 9.4% and ~9% on the two earlier fetches. Three
independent samples inside a point of each other makes "roughly 9-10% of the market" a property of
the market, not of a snapshot; ADR-0023 rests on it and does not need revisiting. The deployed
35-term seed flags **475 (22.2%)** — one posting in five now carries a demotion, a heavier thumb on
`match_score` than the rule was carrying when it was written. Worth watching, not yet worth
changing.

**Scarcity holds.** Titles word-matching "Analytics Engineer": **7 of 2,137**, 0.3% of the corpus,
against 6 and 5 before — the share has not moved across three fetches. "Analyst" appears in 79,
"Data Engineer" in 10, "Data Analyst" in 9. The supply argument stands: no amount of better scoring
produces more of them.

**Concentration holds.** **78 of 126 companies** with a live posting produced no title-matched role
at all — 62%, against 73% and 65% before. The three largest boards contribute 271 of 2,137
postings (13%), so volume and relevance remain unrelated.

**Title match rose to 6.1%** (131 of 2,137) on the real 31-pattern seed, against 3.6% under the
seven-pattern set the warehouse had been running. Still a nudge, not a gate.

### What the original pass could not measure

The V2 extraction fields did not exist when this file was written. They turn two of its arguments
into measurements.

**Instruction 3 is the strongest result here, and it reproduces.** Of postings *titled* Manager,
Lead or Head, **219 describe no people management against 141 that do**, with 19 unclear — so
**60.5% of manager-titled postings are individual-contributor work**, against 58% on the previous
corpus. The error runs the other way too: **127 of 982 (12.9%)** postings not titled that way do
manage people, against 13%. Two independent fetches agreeing twice makes this a property of how
companies name roles rather than a sampling artefact, and it is the org-chart claim at the top of
this file with a number under it.

**Instruction 2's predicted leak is real and sized.** Of 1,361 gold postings the location gate
admitted, **166 (12.2%) are `us_only`** on the description — the rule-based gate cannot see it,
which is what the instruction argued.

**The fit-score distribution moved when the corpus did, and that settles a question.**

| fit_score | 9 evidence units | 29 evidence units |
|---|---|---|
| 1 | 722 | 628 |
| 2 | 103 | **528** |
| 3 | 399 | **140** |
| 4 | 41 | 36 |
| 5 | 28 | 29 |

An earlier reading of the left-hand column called the trough at 2 unexplained and warned against
tuning the rubric to it. Correct call: scoring the same postings against the full corpus moved the
trough to 3. It was an artefact of thin evidence, not a property of the market — given the whole
history the model stops parking postings at 3 and commits them to 2. What barely moved is the top:
**69 postings scored 4-or-5 before, 65 after**. The corpus fix re-sorted the middle and confirmed
the shortlist.

### The extraction prompt was worth 5× on eligibility

`geo_restriction` split by extraction date isolates the fix that told the model to treat the job
board's own location field as authoritative:

| extracted | rows | `unclear` |
|---|---|---|
| under the old prompt | 1,721 | **32.5%** |
| under the fixed prompt | 555 | **6.8%** |

The same posting population, one prompt change, and unresolved eligibility drops by nearly five
times. It also means **gold currently mixes two extraction vintages**: the incremental guard
compares text, model and scoring-prompt version, none of which a changed *extraction* prompt moves,
so 559 rows still carry an `unclear` that the current prompt would very likely resolve. That is the
concrete case for the full refresh in `TODO.md` — it now has a number attached instead of a
principle.

### Do the two rankings disagree enough to be worth keeping both?

Label-free, and the most decision-relevant thing here. Postings split into quartiles by
`match_score`, against the LLM's verdict:

| keyword quartile | n | avg fit_score | fit ≥ 4 |
|---|---|---|---|
| 1 (lowest) | 341 | 1.43 | 2 |
| 2 | 340 | 1.41 | 2 |
| 3 | 340 | 1.73 | 5 |
| 4 (highest) | 340 | 2.47 | **56** |

Three things follow. **The keyword score already finds most of what the LLM rates highly** — 56 of
65 high-fit postings, 86%, sit in its top quartile, so the cheap ranking is not being embarrassed.
**Its bottom half has no resolution at all**: quartiles 1 and 2 are indistinguishable (1.43 against
1.41), so ordering within them is noise. And **nine high-fit postings sit outside the top quartile**
— that, plus reordering inside it, is the LLM's entire marginal contribution.

Nineteen of the 56 top-quartile high-fit postings carry **no title match**, which is the earlier
org-chart argument showing up in the ranking itself: the keyword score reached them through
technology hits alone.

Whether nine recovered postings and a re-sorted top quartile justify the scoring cost is exactly
what human labels decide. The point of measuring it now is that the question has a size:
**precision@k on the top quartile is where the two rankings actually differ**, so that is where the
labelling effort should concentrate rather than being spread evenly across 1,361 rows.
