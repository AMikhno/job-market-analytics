# TODO

What is next and in what order. Shipped work is in git history and in `docs/decisions/`; how the
system works today is in `ARCHITECTURE.md`.

## Priority

Ordered by value per unit of work. The target profile is mid-size SaaS with a real internal data
function — the shape that measurably converts (`docs/research/relevance-signals.md`).

| # | Work | Why it is here | Cost |
|---|---|---|---|
| 1 | **V2 scoring** | Relevance is the measured constraint: 10,170 postings fetched → 1,179 gold → 40 title-matched, and those 40 were largely the *wrong* 40. Also the instrument that makes every later expansion self-evaluating instead of costing a manual evening | Scoped — ADR-0020, `docs/v2-plan.md`; backfill cost pending re-measurement (ADR-0025) |
| 2 | **More companies on already-built ATS** | No engineering at all. The best measured conversion came from a mid-size company on an existing adapter — 11 postings, 4 worth applying to. Staffing and recruiting agencies count as a deliberate company type | List work |
| 3 | **Aggregator source** | Fixes list representativeness at the root: stops requiring the curated list to be a fair sample of the market. After V2, because without scoring it is ten times the noise for the same manual triage | ADR-0017, gates unmet. Main cost is content-based dedup, since `job_key` is `(source, company, external_id)` — ADR-0008 |
| 4 | **List repair** | Wrong data rather than missing adapters, so it is free at runtime | Small — see below |
| 5 | **Careers-page change signal** | Hash each careers page monthly and surface *changed* companies; never synthesize postings. Low competition on that tier is real, but extraction is not how to reach it | ~50 lines — `docs/research/careers-page-tail.md` |
| 6 | **Workday** | A different access pattern from every current source: POST body, offset pagination, and a multi-segment ref instead of a slug. Its boards are also the profile with the worst measured conversion, and one large tenant alone adds ~17 minutes of serial fetching | `docs/research/workday-ref-discovery.md` |
| 7 | ~~Custom careers pages as an ingestion source~~ | **Declined.** Of 40 sampled, 0 carry `JobPosting` JSON-LD and 2 have anything parseable. Parsing was never the bottleneck — most such pages list no openings at all | — |

**Not on this list:** Indeed (its API is closed and scraping is against its terms) and per-company
scrapers (ADR-0013 — they also decay silently, which is the failure mode this repo is built
against).

## V2 — scoped, ready to build

Scope is fixed by ADR-0020; the implementation contract is `docs/v2-plan.md`. Execute its work
items top to bottom, one commit each.

**Read `docs/research/relevance-signals.md` before writing the prompt.** The LLM pass it records
produced four instructions the current signals cannot express — most importantly that scoring the
*requirements* section beats scoring the whole posting by roughly 3/13 against 1/21, because every
corporate job mentions dashboards and only a data job requires dbt. Without those, V2 rebuilds the
keyword matcher that exercise argued against. Note the ratios are that pass's own selections, not
human labels, and cannot double as V2's evaluation set — see the provenance note in that file.

- [ ] Profile config: `shared/profile.py`, `config/profile.example.yaml`, prompt rendering with
      `PROMPT_VERSION` provenance, and the gitignore guard for the real file
- [ ] `int_jobs_structured` — typed extraction, `content_hash` incremental guard for cost control,
      delimiter defense against untrusted posting text, dev-target stub
- [ ] `int_jobs_scored` — 1–5 fit score, profile as a static cacheable prefix,
      model/prompt-version/scored-at provenance, `accepted_values` test
- [ ] Gold and digest become score-aware: the score orders delivery and never filters it
      (ADR-0020); unscored postings still ship
- [ ] Verify the first-backfill cost against the estimate

## List repair

Rows discovery cannot place, kept as classes because each is worth recognizing again. Per-company
detail is in the private list's `notes` column.

- [ ] **Two ecosystem/portal rows** — a tech hub and a regional directory, each listing
      *member-company* jobs, so discovery attributes someone else's board to them. Neither is an
      employer; both are deletion candidates
- [ ] **Two with a confirmed ATS but no extractable token** — the platform is certain from the
      careers page, but no board name appears in the markup and the API probe missed every
      guessable form. Needs a human with the network tab open
- [ ] **One crawl that landed on a different company** — its detected ATS is unverified
- [ ] **One closed tenant** — the board URL redirects to the vendor's home page. Remove or re-point
- [ ] **Junk refs from discovery** — several rows store a URL path fragment instead of a board name

## Parked, with the gate that would unpark it

- **openjobdata** — decisive gate is a real Ottawa-coverage pull from the dataset; then licence,
  identity, cadence and lifecycle mapping (ADR-0017, `docs/research/openjobdata.md`)
- **BreezyHR** — only if a keyless route to a description appears. Four paths tried; today the
  list alone would land rows that can never be ranked (ADR-0021)
- **Embeddings** — deferred: as a cost pre-filter they save pennies at this scale, and
  cross-source dedup is moot while each company sits on one ATS (ADR-0020)
- **Auth-gated ATS** (iCIMS, Teamtailor, SuccessFactors, Dayforce, ADP, UKG, JazzHR, Phenom) —
  no keyless feed exists; they stay inventory-only (ADR-0013)
- ~~**Storage levers**~~ — **retired, not deferred.** The bytes are now converted to dollars: at
  the shipped 180-day retention the steady state costs on the order of **$1/month**, and the two
  remaining levers are worth about $1.30/month between them — under $4 even if the assumed rate is
  triple the real one. Engineering time is the more expensive resource. Reopen only if the volume
  changes by an order of magnitude (`docs/research/ingestion-cost.md`)

## Human-owned

Agents do not run `gh`. Step-by-step versions live in the private runbook.

- [ ] Arm the dead-man's switch: create the healthchecks.io check and add its ping URL as the
      `HEALTHCHECK_URL` secret (period 1 day, grace ≥ 6h, for a twice-daily cron plus DST drift).
      Until set, the step logs "disabled" and the switch is not armed
- [ ] Re-push `COMPANIES_CSV_CONTENT` from `config/companies.active.csv` after any branch that
      changes what the list may contain — deployed code must understand the list before CI gets it
