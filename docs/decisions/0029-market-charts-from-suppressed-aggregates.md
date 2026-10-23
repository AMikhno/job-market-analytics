# 0029 — The site's market charts publish suppressed aggregates, handed between workflows

**Status:** accepted (refines ADR-0028 for the market vocabularies)

The project site shows charts of what the pipeline sees on the boards it tracks — a curated
list of companies, not the job market: open postings over time, how long
a posting stays up, what data roles ask for, role families, and the ATS mix. Publishing
warehouse data on a public page raises three questions the rest of the project had not had to
answer: *what* may leave the warehouse, *which* workflow may carry it, and *how* it is drawn.

## What may leave

**The charts read upstream of the location filter.** `silver_jobs` drops every posting outside
the allowed locations, and those locations are private config (ADR-0028). An aggregate of a
location-filtered table publishes where the filter points as surely as the seed would. So the
market models start from `int_jobs__unioned` — every posting on every tracked board — and none
of them carries location at all. `int_market__postings` re-derives the lifecycle at posting grain
with a group-by rather than reusing silver's window, which is a few lines of repetition bought
for a hard guarantee that no filter sits upstream.

**Every published row covers at least five distinct companies.** Below that, a row describes
one employer's hiring. The floor is a dbt var (`market_min_companies`) applied in each
`agg_market_*` model, and asserted on each model's output by a schema test. It applies to the
companies a figure *rests on*, not its posting count: a technology named by a single employer is
withheld however many postings name it. An ATS below the floor folds into "other" rather than
vanishing, so totals still add up.

**The export re-checks rather than trusts.** `deliver/market_snapshot.py` selects exactly the
fields declared on the `Market*` models in `shared/models.py`, which forbid extras, and each
model re-asserts the floor. A column added to a dbt model does not reach the page until someone
declares it in Python; a suppression bug fails the export instead of publishing. The Python
floor is a second copy of one number, so a test fails if the two drift.

**The market vocabularies are public seeds.** The role-family patterns and the data-stack term
list are tracked, unlike the four filter seeds. They describe the market the charts summarize
— a generic list of data technologies and data job titles — rather than anyone's preferences.
`.gitignore` admits them by name (`market_*.csv`), so the private seeds stay deny-by-default.

## Which workflow carries it

Only Ingest can read the warehouse; only Docs can publish Pages (ARCHITECTURE.md's automation
split). Letting either do the other's job would give one workflow the union of both permission
sets. Instead Ingest exports `market.json` as a workflow artifact, and Docs — triggered by
Ingest completing, as well as by a push — downloads it. Docs gains `actions: read` and nothing
else; Ingest gains nothing, since uploading an artifact needs no token scope.

The snapshot is public by design, so the artifact being downloadable from a public repo's run
page discloses nothing the site does not.

**Absence is handled by event.** After an Ingest run, that run's snapshot must exist — the export
is one of its steps — so a missing one fails the Docs build. After a push, the latest successful
run's snapshot is used, and if none is retained the site publishes without charts and the run
records a warning: a documentation fix should not wait on the data, and a pipeline that has
stopped producing snapshots is already caught by the dead-man's switch.

## How it is drawn

The charts are inline SVG rendered at build time by `scripts/site_charts.py`: five
single-series charts that change twice a day do not justify a JavaScript chart runtime or a
CDN dependency, and a pure function is testable where a browser runtime is not. Every chart
ships a table view, so hover never gates a value.

**Tenure is a Kaplan-Meier curve, not an average.** A mean of "days up" is biased short twice
over: postings still open have not finished their spell, and postings already up when their
board was first read have an unknown start. Kaplan-Meier keeps the first group in the at-risk set
until their observation ends; the second is excluded. The curve's tail is cut where the at-risk
set rests on fewer companies than the floor. "New postings" excludes the same left-censored
group, or every board's first ingest would read as a hiring spike.

## Limits

The tracked boards are a curated list, not a sample of the labour market, so the charts describe
those boards and the page says so. Role families come from title patterns and inherit their
blind spots — a title that names no family lands in "Other". Tech mentions are word matches on
posting text, so "required" and "nice to have" count the same; telling them apart is the V2
extraction's job.

**Cost.** Five small tables rebuilt each run, one extra query pass at the end of Ingest, and a
second trigger on Docs.
