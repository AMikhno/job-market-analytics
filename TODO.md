# TODO

What is next and in what order. Shipped work is in git history and in `docs/decisions/`; how the
system works today is in `ARCHITECTURE.md`.

## Priority

Ordered by value per unit of work. The target profile is mid-size SaaS with a real internal data
function — the shape that measurably converts (`docs/research/relevance-signals.md`).

| # | Work | Why it is here | Cost |
|---|---|---|---|
| 1 | **V2 validation** | The scorer is built and running in prod; nothing yet shows it beats the keyword baseline. Relevance is still the measured constraint: 10,170 postings fetched → 1,179 gold → 40 title-matched, and those 40 were largely the *wrong* 40. Validation is also what makes every later expansion self-evaluating instead of costing a manual evening | Human labels, then `make evaluate` — see [below](#v2--built-unvalidated) |
| 2 | **More companies on already-built ATS** | No engineering at all. The best measured conversion came from a mid-size company on an existing adapter — 11 postings, 4 worth applying to. Staffing and recruiting agencies count as a deliberate company type | List work |
| 3 | **Aggregator source** | Fixes list representativeness at the root: stops requiring the curated list to be a fair sample of the market. After V2, because without scoring it is ten times the noise for the same manual triage | ADR-0017, gates unmet. Main cost is content-based dedup, since `job_key` is `(source, company, external_id)` — ADR-0008 |
| 4 | **List repair** | Wrong data rather than missing adapters, so it is free at runtime | Small — see below |
| 5 | **Careers-page change signal** | Hash each careers page monthly and surface *changed* companies; never synthesize postings. Low competition on that tier is real, but extraction is not how to reach it | ~50 lines — `docs/research/careers-page-tail.md` |
| 6 | **Workday** | A different access pattern from every current source: POST body, offset pagination, and a multi-segment ref instead of a slug. Its boards are also the profile with the worst measured conversion, and one large tenant alone adds ~17 minutes of serial fetching | `docs/research/workday-ref-discovery.md` |
| 7 | ~~Custom careers pages as an ingestion source~~ | **Declined.** Of 40 sampled, 0 carry `JobPosting` JSON-LD and 2 have anything parseable. Parsing was never the bottleneck — most such pages list no openings at all | — |

**Not on this list:** Indeed (its API is closed and scraping is against its terms) and per-company
scrapers (ADR-0013 — they also decay silently, which is the failure mode this repo is built
against).

## V2 — built, unvalidated

The code is done and running in prod. **Nothing yet shows it beats the keyword score**, and that
is the only question left worth answering: a scorer that is confidently wrong looks exactly like
one that is right until human labels say otherwise.

Two rankings run side by side on purpose — `fit_score` (LLM, 1–5) and `similarity` (embeddings) —
because choosing between them without measuring is how a project ends up carrying both forever.

**`make evaluate` does not yet grade `similarity`.** It compares `fit_score` against `match_score`
and hardcodes a two-way verdict, so the embedding ranking currently ships to the digest ungraded.
Until it is a third ranking in that report, running embeddings produces a column nothing measures.

### Next, in order

1. **Master resume corpus** → `config/resume.yaml` (gitignored). Template and the reasoning behind
   its shape are in `config/resume.example.yaml`. The current file is one tailored version, so it
   is lossy by construction: a posting missed because the corpus omitted the relevant work is
   indistinguishable from one correctly scored low. Tagged evidence runs 8 / 4 / 3 across
   analytics-engineer / gtm-engineer / forward-deployed-engineer, which measures the tailoring
   rather than the experience.
2. **Human labels** → `make labels-template` writes a worksheet from live gold; fill the
   `relevant` column with yes/no, blank to skip; then `make evaluate`. A few hundred is plenty.
   The LLM pass in `docs/research/relevance-signals.md` cannot substitute — grading an LLM scorer
   against LLM labels rewards reproducing its predecessor's mistakes.
3. **Grade `similarity` in `make evaluate`** — a third `Ranking` in `evaluation/report.py` and an
   n-way verdict in place of the two-way one. Before embeddings go on, not after: the point of
   running both is the comparison, and an ungraded column is just cost.
4. **Turn embeddings on**: create the CLOUD_RESOURCE connection and the remote model
   (`docs/v2-plan.md`), then set `enable_embeddings: true`. Until then `int_jobs_matched` emits an
   empty stub, so `similarity` is null and delivery falls back to the LLM score and `match_score`.
   No `--full-refresh` is needed for this one: the stub already materialized the table with the
   right column shape and no rows, so the ordinary incremental run backfills every posting.
5. **Full refresh once the corpus lands** — `dbt build --full-refresh --select int_jobs_structured+`.
   Also what the geo_restriction fix needs to take effect: the incremental guard compares text,
   prompt version and model, none of which a changed *extraction prompt* moves.

**Deliberately held, not pending: keep both rankings for ~a month of live runs before deleting
either.** A single precision@k pass on one labelling session decides the question on one day's
data; a month shows whether the winner stays the winner as the list and the market move. The
holding cost is one embedding call per new posting, which is the cheaper half of the pair. Revisit
once the labels exist *and* the month has run — then remove the losing ranking, its column and its
cost, and re-measure `docs/research/relevance-signals.md` against the real corpus, whose numbers
predate both the corpus and any human label.

**Read `docs/research/relevance-signals.md` before writing the prompt.** The LLM pass it records
produced four instructions the current signals cannot express — most importantly that scoring the
*requirements* section beats scoring the whole posting by roughly 3/13 against 1/21, because every
corporate job mentions dashboards and only a data job requires dbt. Without those, V2 rebuilds the
keyword matcher that exercise argued against. Note the ratios are that pass's own selections, not
human labels, and cannot double as V2's evaluation set — see the provenance note in that file.

- [x] Resume corpus: `shared/resume.py`, `config/resume.example.yaml`, prompt rendering with
      `PROMPT_VERSION` provenance, and the gitignore guard for the real file
- [x] `int_jobs_structured` — typed extraction, `content_hash` incremental guard for cost control,
      delimiter defense against untrusted posting text, dev-target stub
- [x] `int_jobs_scored` — 1–5 fit score, resume prompt as a static cacheable prefix,
      model/prompt-version/scored-at provenance, `accepted_values` test
- [x] Gold and digest become score-aware: the score orders delivery and never filters it
      (ADR-0020); unscored postings still ship
- [x] `make evaluate` — precision@k of the LLM ranking against the keyword one
- [x] `int_jobs_matched` — embedding similarity against the resume corpus, behind
      `enable_embeddings` until its remote model exists

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
- **Embeddings as a cost pre-filter or a dedup key** — deferred, and distinct from the embedding
  *ranking*, which is built (`int_jobs_matched`, above). As a pre-filter they save pennies at this
  scale, and cross-source dedup is moot while each company sits on one ATS (ADR-0020)
- **Auth-gated ATS** (iCIMS, Teamtailor, SuccessFactors, Dayforce, ADP, UKG, JazzHR, Phenom) —
  no keyless feed exists; they stay inventory-only (ADR-0013)
- ~~**Storage levers**~~ — **retired, not deferred.** The bytes are now converted to dollars: at
  the shipped 180-day retention the steady state costs on the order of **$1/month**, and the two
  remaining levers are worth about $1.30/month between them — under $4 even if the assumed rate is
  triple the real one. Engineering time is the more expensive resource. Reopen only if the volume
  changes by an order of magnitude (`docs/research/ingestion-cost.md`)

## Human-owned

Agents do not run `gh`, and are blocked from creating cloud resources or deleting datasets — so
anything below needs a person even when the code around it is finished. Step-by-step versions live
in the private runbook.

- [ ] Arm the dead-man's switch: create the healthchecks.io check and add its ping URL as the
      `HEALTHCHECK_URL` secret (period 1 day, grace ≥ 6h, for a twice-daily cron plus DST drift).
      Until set, the step logs "disabled" and the switch is not armed
- [ ] Re-push `COMPANIES_CSV_CONTENT` from `config/companies.active.csv` after any branch that
      changes what the list may contain — deployed code must understand the list before CI gets it
- [x] `RESUME_YAML_CONTENT` set as an encrypted **secret** (not a variable — the repo is public and
      the corpus is real PII, ADR-0027). Without it the scheduled run skips landing the resume and
      ships unscored postings on the V1 ordering, which is the intended degraded state, not a fault
- [x] `v0.2.0` tagged and pushed, after the merge rather than before, so the tag names a commit
      that is actually on the default branch
- [ ] **Embeddings infrastructure — three steps, once.** Everything else is built and gated behind
      `enable_embeddings`, which stays `false` until these exist; the model emits an empty stub
      meanwhile, so nothing fails.

      ```bash
      # 1. the connection (an agent is blocked from creating cloud resources)
      bq mk --connection --location=us-central1 \
        --project_id=job-search-pipeline-prod --connection_type=CLOUD_RESOURCE vertex

      # 2. grant its auto-created service account access to Vertex
      SA=$(bq show --connection --format=json \
        job-search-pipeline-prod.us-central1.vertex \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["cloudResource"]["serviceAccountId"])')
      gcloud projects add-iam-policy-binding job-search-pipeline-prod \
        --member="serviceAccount:$SA" --role="roles/aiplatform.user"

      # 3. the remote model the SQL references (dbt cannot create this itself)
      bq query --use_legacy_sql=false --location=us-central1 \
        "CREATE OR REPLACE MODEL \`jobs_silver.text_embedding\`
         REMOTE WITH CONNECTION \`us-central1.vertex\`
         OPTIONS (ENDPOINT = 'text-embedding-005')"
      ```

      Then set `enable_embeddings: true` in `dbt/dbt_project.yml`. Grant propagation can take a
      minute or two, so a first run immediately after step 2 may fail on permissions.
