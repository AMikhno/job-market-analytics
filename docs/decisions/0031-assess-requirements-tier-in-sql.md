# 0031 — The model judges work and tooling; SQL assigns the tier

**Status:** accepted, off by default until measured (amends ADR-0020 on how delivery presents a
posting; the fit score of ADR-0027 runs beside it until `make evaluate` separates them)

## What the existing signals cannot do

Two things already rank a posting, and each fails in a way the other does not cover.

**The keyword seeds flatten what they count.** A posting that names one business-intelligence tool
and a posting built around that vendor's whole ecosystem, with years of it required, are both one
hit. A title seed has the mirror defect: a role titled as one discipline whose described work is
another is matched, or missed, on the title. The seeds are not wrong, they are coarse — the
distinctions that decide whether a posting is worth an application are ones a term list cannot
express.

**The 1–5 score cannot be argued with.** `int_jobs_scored` returns one number for a whole posting.
Nothing says what drove it, so a wrong score can be noticed and never diagnosed, and five rungs
for a whole corpus leave most of the order tied.

## Decision

Ask the model for the two judgements a keyword cannot make, and compute everything else.

**`int_jobs_assessed` judges two things**, from the posting's requirements and responsibilities
against the candidate's work bullets:

| Field | Values | The distinction it draws |
|---|---|---|
| `work_match` | same · close · adjacent · different | What the day-to-day work actually is. The title is passed as a hint only; where it and the described work disagree, the work decides |
| `stack_gap` | none · substitutable · central | Whether a missing tool is a direct equivalent of one the candidate uses, or an ecosystem the role is built around |

It also returns the years of people management a posting requires, and one sentence each for the
strongest overlap and the most important gap.

**`int_jobs_tiered` reads the level and assigns the tier.** The level is not asked of the model. It
comes from columns that already exist — seniority words in the title, the years the posting
requires, and whether it manages people — because each is a fact a rule reads more consistently
than a model summarises. The tier is then an ordered rule, worst first:

| Tier | First match wins |
|---|---|
| `drop` | a different kind of work, or a level far out of range (an executive title, or several years of people management required) |
| `stretch` | a neighbouring discipline, or a role built around an ecosystem the candidate does not use |
| `fit` | mostly the candidate's work, or an equivalent tool, or a level that is not the best one — junior, staff, or leading a team |
| `best_fit` | the candidate's own work, with their own tools, at mid or senior level |

`drop` is deliberately narrow. An unusual level alone never drops a posting; it only keeps it out
of `best_fit`.

The rule is SQL because that is what makes it checkable: every branch is pinned by a dbt unit test,
its thresholds are vars, and because the tier is a view over stored judgements, changing the rule
re-tiers every posting on the next run without a model call.

**It needs no configuration of its own.** The candidate text is the resume corpus without the
scoring rubric, and the tool list is the existing `desired_tech` seed.

## Delivery

The digest is banded by tier — best fit, fit, stretch, then postings not yet assessed — with the
two sentences under each posting. **`drop` is collapsed to one line: a count and the titles,
without links.**

This amends ADR-0020, which had the score order delivery and never filter it. The amendment is
narrower than it looks. Nothing is deleted: the rows are untouched in gold, and the titles stay in
the email because the tier is a model's reading and the only way to catch a posting wrongly
dropped is to see its name. What changes is that a posting in a different profession no longer
takes a full line beside one worth reading.

The guarantee that mattered in ADR-0020 is kept: with the assessment off, failing, or behind, the
tier is null and the digest is the flat list it was. The location filter is untouched and plays no
part in the tier.

## Alternatives

- **Give the model more to go on: a list of things the candidate cannot claim, and lists naming
  their field.** Rejected on both counts. What can be claimed is a question about writing an
  application, not about whether a posting fits; and the field is already described by the work
  bullets, so a second description is one more thing to keep in step with them.
- **Refine the seeds.** Free, and it cannot express either distinction above however long the
  lists grow.
- **Reword the scoring prompt.** Cheapest change to the model call, and it still returns a number
  with no reason.
- **Have the model return the tier.** One call and no view, but the rule becomes part of a prompt:
  untestable, and changing it re-bills every posting.
- **Have the model judge level too.** Fewer moving parts in SQL, but level words, required years
  and reporting lines are already columns, and a rule over them does not drift between runs.
- **Assess outside the warehouse with a stronger model.** Likely the most accurate reading, at the
  cost of a credential, a second runtime and the SQL-only shape of ADR-0004. Held in reserve: it is
  the next step only if this design fails the measurement below.

## Cost and invalidation

One generation per posting text, on the flash tier rather than flash-lite, incremental on
`content_hash`. A stored assessment stands only while its three inputs are current: the candidate
text, the instruction (`assessment_version`) and the endpoint. The instruction is compared
explicitly because the extraction model does not compare its own, and mixed two prompt vintages in
one column as a result.

## What is not known

Whether it works. The design is argued, not measured: the BigQuery branch of `int_jobs_assessed`
has been parsed and not executed, and no tier has been compared with a label. `enable_assessment`
is false for that reason. The acceptance test is `make evaluate`, which grades the tier as a ranking
beside the other three and compares it with the labels' own four grades — in particular, how many
postings labelled as worth applying to the tier would have dropped.

Whether the tool list earns its place is also open. The bullets name tools already; the seed is
included because it is free to pass, and the same measurement can be repeated without it.
