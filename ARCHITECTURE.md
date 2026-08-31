# Architecture

How the job-matching pipeline works. Decisions and their alternatives are in `docs/decisions/`;
what is planned next is in `TODO.md`.

Two layers, and the lower one stands alone. **V1 is ingestion plus dbt transformations — no LLM,
no scoring**: it produces a deduplicated, rule-filtered table of postings from every ATS with a
public, keyless feed, on a dual-target dbt project — DuckDB for development, BigQuery for
production, from the first commit, so there is no migration later. **V2 adds AI relevance on top**
(ADR-0020): prod-only models that order delivery and never filter it, so with them absent, failing
or unscored, the pipeline still ships the V1 result rather than nothing. That is why the dev target
stubs them instead of requiring a model to build the DAG.

## Shape

```
 Python                       dbt — three zones, one DAG
┌────────┐   ┌─────────┐   ┌───────────────────────────────────┐   ┌──────┐
│ INGEST │ → │ BRONZE  │ → │              SILVER               │ → │ GOLD │ → DELIVER
└────────┘   └─────────┘   │  int_jobs__unioned    (view)      │   └──────┘   (links)
 ATS APIs     stg_* views  │  silver_jobs          (table)     │    fct_job_postings
 1 table/src               │  ┄ AI: extract → score, match     │    (table)
 + run meta                └───────────────────────────────────┘
```

Bronze, silver and gold map one-to-one onto dbt's staging → intermediate → marts. Silver is the
intermediate *zone*, not a single layer: it holds several chained models, and V2's AI models are
intermediate models inside it rather than new top-level layers (ADR-0010). Each model does one
distinct job, so the chain earns its length.

| Zone | Model | Materialized | Does |
|---|---|---|---|
| Ingest (Python) | `raw_<source>_jobs`, `ops.ingest_runs` | — | Normalize, land, record run metadata |
| Bronze / staging | `stg_<source>__jobs` | view | Cast and standardize one source's landing |
| Silver / intermediate | `int_jobs__unioned` | view | Union + `job_key` + `content_hash` + `clean_text` |
| Silver | `silver_jobs` | table | Dedup, location filter, soft signals, lifecycle |
| Gold / marts | `fct_job_postings` | table | One row per *live* posting, ranked, with the link |
| Deliver (Python) | `deliver/digest.py` → `ops.digest_runs` | — | Email postings new since the last digest (ADR-0019) |

## Ingestion

One adapter per access method, not per company. There are 9 today. <!-- check:sources -->

Every adapter returns the same `RawPosting` (`shared/models.py`), so dbt downstream sees one shape:
source, company, external id, title, location, remote policy, department, employment type, url,
description HTML, posted-or-updated timestamp, and the untouched original payload.

Two access shapes exist. Most sources return a whole board in one GET. The rest omit the posting
body from that response, so each posting costs a second call — **a V1 source must yield a
description**, because silver's deal-breaker and desired-tech signals both read it, and a
description-less row would be permanently unrankable (ADR-0021). A source with no keyless route to
a description is not adopted.

Boards are fetched concurrently with the minimum interval enforced **per host** rather than
globally, and rows landed on the main thread because DuckDB takes a single writer (ADR-0022). That
is what keeps per-posting detail calls affordable: ATS that give each company its own subdomain
overlap almost perfectly, while ATS sharing one host stay exactly as paced as before.

Adapters parse strictly. Schema drift raises rather than landing zero rows, because a source that
quietly returns nothing looks identical to a company with no openings.

### The company list

These APIs have no cross-company or location search: you query one board at a time by its
reference. That list is **private config** read by the Python ingest, not a dbt seed (ADR-0011),
committed only as `config/companies.example.csv`.

`board_ref` is the ATS-specific path fragment its adapter interprets — usually a bare token, but
multi-segment where a platform needs one (ADR-0012). Each active ref is format-checked at load,
before any fetch, so a pasted URL fails loudly; note this also means one malformed ref fails the
whole run rather than skipping one board. Companies on an unsupported ATS stay in the list as
inactive inventory, so coverage gaps are visible rather than forgotten.

Discovery is manual (`make discover`) because no reliable discovery API exists: the tool renders a
company's own site and reads which ATS it actually calls, escalating through five steps before
giving up (ADR-0018). Restaging **merges** rather than overwrites, so a hand-corrected reference
survives a refresh and an apparent ATS move is reported as a conflict for a human.

## Keys, dedup and lifecycle

- **`job_key`** — surrogate of `(source, company, external_id)`. Stable identity; silver keeps the
  latest row per key. Cross-source duplicates are moot while each company uses one ATS.
- **`content_hash`** — surrogate of `(title, clean_text)`. Changes when the text changes. This is
  V2's incremental reprocessing key, not the dedup key (ADR-0008).

Silver derives three lifecycle columns. `first_seen_at` is the earliest run that saw a posting and
is what "new since last run" means — the source APIs offer no server-side date filter, so every run
pulls the whole board and novelty is *derived*, never requested. `last_seen_at` is the most recent
run that still contained it. `is_active` requires both that the posting was in its board's latest
ingest **and** that the board itself ingested recently, so a board that is removed or persistently
failing ages out of gold within a day instead of leaving zombie postings behind.

`silver_jobs` is the durable record of every posting, live or closed; gold shows only live ones
(ADR-0016).

## Filtering: one rule removes, the rest rank

**Allowed location is the only hard filter.** It is deliberately coarse — keep a posting whose
location is unknown, is bare "Remote", or word-matches an allowed marker; drop the rest. Unknown
means null *or* blank, because a board that omits the field and one that publishes an empty string
mean the same thing. It cannot tell "Remote" from "Remote (US)", which is a known limit, not an
oversight — and matching is ASCII word-boundary, so an accented place name needs both spellings
seeded or an abbreviation that covers the region.

Everything else annotates and orders: `desired_tech_hits`, `title_match`, and deal-breaker
technologies, which **demote rather than delete** (ADR-0023) because postings naming an unwanted
tool are frequently still worth seeing. These collapse into one visible `match_score` that the
digest sorts by, so the number shown is the number sorted on (ADR-0024).

All of it is seed-driven data, not code, so tightening a rule is an edit to a seed. The rules
themselves are private config (ADR-0028) — `make seeds` materializes `dbt/seeds/*.csv` from an
Actions variable, a private local file, or the committed `config/seeds/*.example.csv`, in that
order. A clone with none of them builds on the examples and says so.

What V1 cannot do without a model: tell a requirement from a nice-to-have, infer seniority, or
judge real eligibility. That is exactly V2's scope.

## Rebuilds, not migrations

Everything dbt builds is recreated from raw on every run, which has three consequences worth
stating:

- **Adding or changing a column needs no migration.** The next run materializes the new shape over
  the whole retained history.
- **Changing a seed is retroactive by design.** The next run re-evaluates every posting still in
  raw, which is what you want while the rules are still maturing.
- **The only stateful objects are the raw and ops tables**, which are append-only. Changing them is
  additive nullable columns only, applied before the code that writes them; a rename is a new
  column plus a backfill.

V2's incremental models are the exception — they exist to avoid recomputation, so a schema change
there re-bills a backfill and is a costed decision (`docs/v2-plan.md`).

## Automation

Four workflows, split by **what may trigger them** and **what credentials they may hold**. The
split is the security boundary: merging any pair would give the merged workflow the union of both
permission sets on the union of both triggers.

| Workflow | Trigger | Holds | Does |
|---|---|---|---|
| Ingest | twice-daily cron + manual dispatch | `id-token: write`, prod environment | The pipeline: fetch → land → transform → deliver |
| CI | every push and PR | nothing | Lint, tests, the DuckDB DAG, a prod-target parse, gitleaks |
| Docs | push to `main` | `pages: write` | Publishes the dbt lineage DAG to GitHub Pages |
| Tag release | push to `main` touching `pyproject.toml` | `contents: write` | Publishes `v<version>` if it does not exist yet |

Only Ingest can reach the warehouse: `id-token: write` is what mints the OIDC token that Workload
Identity Federation exchanges for GCP credentials, and no other workflow has it. That is also why
its actions are pinned to commit SHAs rather than tags — a hijacked moving tag in *that* file could
exfiltrate the exchange. CI holds no secrets at all, which is what makes it safe on fork PRs, and
is why CI builds against the example seeds and can say nothing about the rules production is using.

One Ingest run, in order. Every step after authentication is a `make` target, so the same sequence
runs locally:

1. **Authenticate** to GCP via WIF — no key file exists to leak.
2. **Materialize private config** — the company list and the four filter seeds, from Actions
   variables. On the prod target a missing seed variable *fails the run* rather than falling back
   to the committed example, which would filter gold to somewhere nobody lives and still exit 0.
3. **Ingest** every active board into `raw_<source>_jobs`, then **surface warnings** as
   annotations and a run summary.
4. **Compile dbt against BigQuery** before building anything — a dialect error fails here, cheaply,
   instead of halfway through a materialization.
5. **Land the resume corpus** and the rendered scoring prompt, if the secret is present. Absent, the
   step is skipped and postings ship unscored on the V1 ordering — the honest degraded state.
6. **Transform** bronze → silver → gold, then run the **freshness gate**.
7. **Deliver** the digest of postings new since the last watermark, or a heartbeat if none.
8. **Ping the dead-man's switch**, only on full success.

The workflows are themselves linted (`make workflow-lint`). An invalid workflow is not a failed
run — GitHub records a zero-second startup failure, stops honouring the schedule, and sends no
failed-run email, because no run ever started. Silence is indistinguishable from health, so the
validation has to happen before the file lands.

## Health

Four layers, because each catches something the one before it cannot.

1. **Hard failure** — a source whose every board failed exits non-zero. GitHub's failed-run email
   is the alert; there is no webhook to rot (ADR-0019).
2. **Warnings** — a run stays green but reports what it noticed: a source landing zero rows, a
   source landing far below its own recent median, boards skipped mid-run, and sources registered
   with no boards in the list. Each reaches the run summary, a CI annotation and the digest footer.
   They exist because all four leave the run looking healthy while the posting stream thins.
3. **Sustained staleness** — `dbt source freshness` fails the run when a source has landed nothing
   for 30 hours, escalating what a single run's warning would not.
4. **Total silence** — the first three assume the workflow *runs*. A suspended schedule emails
   nothing, so a successful run pings an external dead-man's switch; the alert fires when pings
   stop, which covers a suspended cron, an earlier failure and a dead runner alike.

Every run writes one `ops.ingest_runs` row per source and a machine-readable summary, so health is
judged from recorded outcomes rather than from whether the cron fired.

## Boundaries

V1 ingestion needs **no credentials** — every source API is public and keyless. The only secrets
are BigQuery authentication, which uses Workload Identity Federation and therefore has no key file
at all, and the digest's SMTP credentials. Both live only in GitHub Actions. The repo holds secret
*names* and placeholders, never values, with `gitleaks` as a pre-commit backstop (ADR-0007).

The company list is private for the same structural reason: it identifies the search, so it lives
in a GitHub Actions variable and never in the tree.

## V2

Three prod-only models inside silver, all SQL against warehouse-native AI functions (ADR-0004),
reading from `silver_jobs` so they only ever see postings that survived filtering. On the DuckDB
target each emits a typed-null stub with the same column shape, so the dev DAG and its unit tests
run without a model.

| Model | Does |
|---|---|
| `int_jobs_structured` | Typed extraction: `company_type`, `geo_restriction`, `manages_people`, minimum years, required and nice-to-have techs, and `relevant_text` |
| `int_jobs_scored` | 1–5 `fit_score` of `relevant_text` against the resume corpus |
| `int_jobs_matched` | Embedding `similarity` of `relevant_text` to the closest single resume bullet, and which bullet it was |

The third answers the same question as the second by different means, and runs beside it on
purpose until labels say which is better. Embeddings take only that half: similarity is blind to
polarity — "we do not sponsor visas" and "we sponsor visas" are nearly identical vectors — so
`geo_restriction` and `manages_people` stay with the LLM and never move there. It is gated behind
`enable_embeddings`, which stays false until its remote model exists; off, the model emits its
stub and `similarity` is simply null.

**Scoring reads the requirements, not the posting.** Extraction drops the responsibilities blurb
and the boilerplate, which measured 570 characters out of 6,854 — about 8% of the text. Every
corporate posting mentions dashboards and stakeholders; only a data role *requires* dbt
(`docs/research/relevance-signals.md`). The trim is also what makes scoring cheap.

**What it is matched against is a resume corpus, not a skills list** (ADR-0027): work-history
bullets, each one an independently embeddable unit carrying who it was built for. A skills list
would make the model do fuzzy keyword matching, which the seeds already do for free.

**The score orders delivery and never filters it** (ADR-0020). Gold joins it `LEFT` and nulls
anything outside 1–5, so an unscored or mis-scored posting still ships and simply sorts lower;
while nothing is scored, delivery is byte-for-byte the V1 ordering.

**Cost is controlled by the incremental guard, not by sampling.** Both models key on
`content_hash`, so the ~24 re-landings each posting accumulates are extracted once. Removing that
guard re-bills the whole backfill.

**The model endpoint is pinned** (ADR-0025). Left managed, an `AI.GENERATE` call answered as a
model from a family retiring 2026-10-20 — so an unpinned call would have changed model mid-life
with nothing recording that it had.

Untrusted posting text is delimited and framed as data rather than instructions at both stages, and
the output is type-constrained, so a posting cannot instruct the scorer.

**Whether any of this beats the keyword score is an open question**, and deliberately so: `make
evaluate` scores a ranking by precision@k over human labels. Labels from an LLM cannot settle it —
grading a scorer against its predecessor's judgements rewards reproducing that predecessor's
mistakes. Until those labels exist, all three orderings are carried and none is trusted.
