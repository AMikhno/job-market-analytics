# Research — openjobdata as an aggregated ingestion source

Reference notes for evaluating **openjobdata** (https://openjobdata.com) as a job-posting
source. This is a findings record, **not a decision** — the decision (and the gates that must
pass before switching) lives in ADR-0017. Captured 2026-07 from the site, its docs, and the
associated scraper repo.

## What it is

A free, worldwide, daily-updated aggregation of job postings scraped from many ATS platforms,
published as Parquet on Hugging Face with **no token required**. It is a *pull-a-dataset* source,
not a per-company API: you download/filter a large file rather than fetching one board at a time.

## Access & format

- **Location:** Hugging Face bucket `Invicto69/Jobs-Dataset-bucket` (public; no auth token).
- **Layout:** `full`-schema and `minimal`-schema variants; a `changes/` directory of **daily
  delta files** named `YYYY-MM-DD.parquet`; plus a **company-metadata** file.
- **Format:** Apache Parquet (columnar — cheap to filter by column without reading all rows).
- **Size:** the full dataset is **~30–40 GB**. Loading/concatenating all shards in memory is
  impractical; the intended pattern is **filter-in-place** (predicate pushdown) or **stream the
  daily deltas** into a database, never load-all.

## Cadence

**Daily.** New `changes/YYYY-MM-DD.parquet` deltas are published each day (fresh listings,
de-duplicated, removals reconciled). Confirm the actual publish reliability in practice (a gate
in ADR-0017).

## ATS coverage

Broad. The associated scraper project (`kalil0321/ats-scrapers`, aka "jobhive") reports **47 ATS,
3.27M+ live jobs, 86k+ companies**, including every platform we care about:

| ATS | Reported volume | In our pipeline today |
|-----|-----------------|-----------------------|
| Greenhouse | ~170K | adapter (V1) |
| Lever | — | adapter (V1) |
| Ashby | — | adapter (V1.5) |
| Workday | ~449K | tentative V2 (ADR-0013) |
| iCIMS | ~121K | deferred (ADR-0013) |
| BambooHR | — | tentative V2 (ADR-0013) |
| SmartRecruiters, EURES, national aggregators, … | large | not planned |

> **Attribution caveat.** These counts come from the *scraper repo*, published to
> `storage.stapply.ai/jobhive`. openjobdata.com serves from HF bucket `Invicto69/Jobs-Dataset-bucket`.
> They appear to be the same/sibling dataset, but **confirm openjobdata.com's own ATS list and
> counts** before relying on them (a gate in ADR-0017).

## Schema & mapping to `RawPosting`

The dataset is a rich superset of our common schema (`shared/models.py:RawPosting`):

| openjobdata field | maps to / notes |
|-------------------|-----------------|
| `title` | `title` |
| `company` | `company` (name; today we key on `board_ref`) |
| `ats_type`, `ats_id` | `source` + `external_id` analog |
| `url` | `url` |
| `description` | `description_html` (confirm HTML vs plain text) |
| `location`, `country_iso`, `region`, `lat`, `lon` | `location` — **structured**, so the silver
  location gate could use `country_iso`/`region` instead of regex |
| `is_remote` | `remote_policy` analog |
| salary, employment type, posting metadata | extra fields (useful in V2 scoring) |
| `global_id` | dataset-native id; our `job_key` would still derive from `(source, company, external_id)` |

Because it feeds the **same** bronze→silver→gold, our filtering, soft signals, and lifecycle
columns are unaffected — only the ingestion source changes.

## License

**MIT** on the scraper repo (`kalil0321/ats-scrapers`). The **dataset's own license/ToS is not
yet confirmed** — and note the postings are *scraped* (vs our current use of official public ATS
APIs), so the terms-of-service story differs. Confirm before adopting (a gate in ADR-0017).

## Open questions (resolve before switching — see ADR-0017)

1. **Ottawa/Canada density** — does it actually contain the Ottawa postings we care about, at good
   coverage? Needs a real data pull (download a delta, filter `region`/`country_iso`, count) — a
   one-off analysis, out of scope for the current no-code stage.
2. **License / ToS** of the dataset itself (not just the scraper repo).
3. **Identity** — confirm openjobdata.com (HF `Invicto69/Jobs-Dataset-bucket`) is the same dataset
   as the jobhive/stapply figures, and its own coverage counts.
4. **Cadence in practice** — is the daily delta actually published reliably?
5. **Lifecycle/dedup semantics** — how removals/dedup map to our `first_seen_at` / `is_active`
   (do we trust their removal signal, or re-derive lifecycle from daily deltas ourselves?).
6. **Niche coverage gap** — small Ottawa companies on ATS outside the ~47 won't be present, so
   custom collection continues regardless (the hybrid model in ADR-0017).
