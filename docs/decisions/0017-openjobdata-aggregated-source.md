# 0017 — Evaluate openjobdata as an aggregated ingestion source (hybrid)

**Status:** proposed / under evaluation

Today ingestion is **fetch-by-`board_ref`**: one adapter per ATS, pulling each target company's
board directly from its official public API (Greenhouse, Lever, Ashby; Workday/BambooHR are
tentative V2 — ADR-0013). **openjobdata** offers a different model: a free, daily, worldwide
Parquet aggregation of ~47 ATS with no token (see `docs/research/openjobdata.md`). It could
supply most postings we want without any per-ATS adapter maintenance — but only if its Ottawa
coverage is real.

## Direction (proposed): hybrid, not replacement

openjobdata would slot in as **a new ingestion source feeding the same bronze→silver→gold**. Our
dedup, keyword/location filter, soft signals, and lifecycle stay; only sourcing changes. The
*model* shifts from **fetch a board** to **filter a big dataset** by company/location.

It is inherently **hybrid**: openjobdata covers ~47 ATS, but many small Ottawa companies use niche
or local ATS (or bespoke career pages) outside that set. Those are **not** in openjobdata, so
**custom collection continues** for the long tail regardless of adoption. The company-discovery
notebook (ADR-0018) is what tells us, per company, "openjobdata-covered vs needs custom collection."

**Fate of the existing Greenhouse/Lever/Ashby adapters is deferred to post-verification.** If
openjobdata's coverage and freshness for our targets prove good, they can retire (adapters only
for the niche tail); if not, they stay as the source of truth for priority companies. We keep them
until verification decides — see ADR-0013, whose adapters this reframes as the *niche fallback*
rather than the main roadmap.

## Concerns / trade-offs

- **Coverage gap:** only ~47 ATS. The niche tail always needs custom collection (above).
- **External-dependency risk:** we no longer own the scrape. If openjobdata stops updating or
  changes access, ingestion for covered companies breaks; our own adapters have no such single
  point of failure.
- **ToS / provenance:** postings are *scraped* by a third party, vs our current use of official,
  public, keyless ATS APIs (a clean ToS story). The dataset's own license/ToS is unconfirmed.
- **Size / cost:** ~30–40 GB. We must **filter-in-place** (predicate pushdown on `region` /
  `country_iso`) or **stream daily deltas** — never load-all. BigQuery can query Parquet via an
  external table or a filtered load; sizing/cost needs a look.
- **Freshness / lifecycle:** their dedup/removal becomes our signal. We must decide whether to
  trust their removal flag or **re-derive `first_seen_at` / `is_active` from the daily deltas
  ourselves** (the latter keeps our lifecycle semantics intact and source-agnostic).
- **Data trust:** third-party normalization quality (locations, dedup, description fidelity) is
  unverified for our slice.

## Verification gates (must pass before switching)

1. **Ottawa/Canada density** — download a delta, filter `region`/`country_iso`, and confirm our
   target companies/postings are actually present at good coverage. This is the decisive gate.
2. **License / ToS** of the dataset (not just the scraper repo).
3. **Identity** — confirm openjobdata.com (HF `Invicto69/Jobs-Dataset-bucket`) is the dataset the
   ~47-ATS / 3.27M figures describe, and its own coverage counts.
4. **Cadence in practice** — daily deltas actually published reliably.
5. **Lifecycle mapping** — how removals/dedup map to `first_seen_at` / `is_active`.

Until these pass, this stays **proposed**; the V1.5 adapters remain the shipped path.

## If adopted (sketch, not built now)

A new `openjobdata` source: a loader that pulls the daily delta(s), filters to Ottawa/Canada +
our target companies, and lands rows in the common `RawPosting` shape into a new
`raw_openjobdata_jobs` table with its own `stg_openjobdata__jobs` staging model — unioned into
`int_jobs__unioned` exactly like the current adapters. No changes to silver/gold.
