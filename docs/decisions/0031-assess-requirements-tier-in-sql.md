# 0031 — The model reads requirements; SQL assigns the tier

**Status:** accepted, off by default until measured (amends ADR-0020 on how delivery presents a
posting; the fit score of ADR-0027 runs beside it until `make evaluate` separates them)

## What the 1–5 score cannot do

`int_jobs_scored` asks a model for one number per posting. Three defects follow from that shape,
and none is fixable by rewording the prompt:

- **There is nowhere to put what the candidate has not done.** The prompt lists accomplishments, so
  a posting requiring something adjacent to them reads as covered. A person judging the same
  posting works from the opposite list: what cannot honestly be claimed.
- **The number cannot be argued with.** A wrong 2 and a wrong 5 look the same in a digest. Nothing
  says which requirement sank the posting, so a bad score can be noticed and never diagnosed.
- **Most of the order is a tie.** Five rungs for a whole corpus puts many postings on each, and the
  ranking inside a rung is whatever the tie-break is.

A fourth is specific to this rubric: it instructs the model to report eligibility and to discount
deal-breaker technologies, but the call returns one number and is never given the list.

## Decision

Split reading from judging.

**`int_jobs_assessed` reads.** One generation per posting text returns facts: the requirements the
posting states as required, which of them the candidate profile does not show — separated into gaps
an employer would plausibly waive and gaps one would not — how the described work relates to the
candidate's field, and how far the described level is from the candidate's. It also returns one
sentence for the strongest match and one for the most serious gap.

**`int_jobs_tiered` judges.** A view turns those facts into a tier with an ordered rule:

| Tier | Rule, first match wins |
|---|---|
| `unrelated` | a different field, or a level further away than `tier_max_level_distance` |
| `reach` | any gap an employer would not waive, or work listed only as an interest |
| `near` | more waivable gaps than `tier_fit_max_soft_gaps`, or a role below the candidate's level |
| `fit` | everything else |

The rule is SQL because that is what makes it checkable. Each branch is pinned by a dbt unit test,
the thresholds are vars, and because the tier is a view over stored facts, changing a threshold
re-tiers every posting on the next run without a model call.

**The profile is a second rendering of the resume**, landed in its own table. It adds what a work
history leaves a reader to guess: a cannot-claim list, years of experience, and three plain-language
lists naming the candidate's core work, adjacent work, and work of interest. All private config, as
the rest of the resume is (ADR-0027).

## Delivery

The digest is banded by tier — fit, near, reach, then postings not yet assessed — with the two
sentences under each posting. **`unrelated` is collapsed to one line: a count and the titles,
without links.**

This amends ADR-0020, which had the score order delivery and never filter it. The amendment is
narrower than it looks. Nothing is deleted: the rows are untouched in gold, and the titles stay in
the email because the tier is a model's reading and the only way to catch a posting wrongly
collapsed is to see its name. What changes is that a posting in a different profession no longer
takes a full line beside one worth reading. The alternative — every posting listed in full,
whatever its tier — is what made a correct lowest score indistinguishable from no scoring at all.

The guarantee that mattered in ADR-0020 is kept: with the assessment off, failing, or behind, the
tier is null and the digest is the flat list it was.

## Alternatives

- **Reword the scoring prompt.** Cheapest, and it leaves all three structural defects in place.
- **Have the model return the tier.** One call and no view, but the rule becomes part of a prompt:
  untestable, and changing it re-bills every posting.
- **Assess outside the warehouse with a stronger model.** Likely the most accurate reading, at the
  cost of a credential, a second runtime and the SQL-only shape of ADR-0004. Held in reserve: it is
  the next step only if this design fails the measurement below.
- **Extract must-haves in `int_jobs_structured` and judge in a second call.** Cleaner separation,
  but it changes the live extraction model and re-bills its backfill before anything is measured.
  The assessment reads the extraction's `relevant_text` instead, so the live models are untouched.

## Cost and invalidation

One generation per posting text, on the flash tier rather than flash-lite, incremental on
`content_hash`. A stored assessment stands only while its three inputs are current: the profile,
the instruction (`assessment_version`) and the endpoint. The instruction is compared explicitly
because the extraction model does not compare its own, and mixed two prompt vintages in one column
as a result.

## What is not known

Whether it works. The design is argued, not measured: the BigQuery branch of `int_jobs_assessed`
has been parsed and not executed, and no tier has been compared with a label. `enable_assessment`
is false for that reason. The acceptance test is `make evaluate`, which grades the tier as a ranking
beside the other three and compares it with the labels' own four grades — in particular, how many
postings labelled as worth applying to the tier would have collapsed.
