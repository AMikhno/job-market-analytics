# 0025 — Gemini 3.1 Flash-Lite, called on the global endpoint

**Status:** accepted (for V2; supersedes 0009)

ADR-0009 chose Gemini 2.5 Flash-Lite to avoid a retiring family. The 2.5 family now retires in
turn (shutdown 2026-10-20), so its own argument applies to it. The reasoning is unchanged —
extraction and scoring are simple structured tasks, so the cheapest capable tier wins — and only
the id moves: **`gemini-3.1-flash-lite`**.

**The model has no regional endpoint.** One `generateContent` call per endpoint, 2026-08-15:

| Endpoint | Result |
|---|---|
| `global` | 200, `modelVersion: gemini-3.1-flash-lite` |
| `northamerica-northeast2` | 400 `FAILED_PRECONDITION` |
| `northamerica-northeast1` | 404 `NOT_FOUND` |
| `us-central1` | 404 `NOT_FOUND` |

No foundational Gemini model runs natively in `northamerica-northeast2`, the original warehouse
location, and Google's in-region Canadian processing (`northamerica-northeast1`) does not serve
this model either — so the choice was between the global endpoint and no model at all. Global is
acceptable: the pipeline reads public job postings and has no residency requirement.

**BigQuery's supported endpoints are not Vertex's**, and that is the practical constraint.
`us-central1` returns 404 for this model at the raw publisher endpoint, yet `AI.GENERATE` reaches
it from a `us-central1` dataset — BigQuery resolves through its own path against its own
allowlist. Probed by calling `AI.GENERATE` with each endpoint, 2026-08-16:

| Endpoint | BigQuery |
|---|---|
| `gemini-3.1-flash-lite` | works — the newest *flash-lite* it accepts |
| `gemini-3.5-flash` | works (newer, but the flash tier) |
| `gemini-3.5-flash-lite` | `Unsupported endpoint` |
| `gemini-3.1-flash` | `Unsupported endpoint` |

The allowlist is non-contiguous, so "newest available on Vertex" is the wrong question. Probe
BigQuery directly before changing `scoring_endpoint`, and prefer an explicit version over the
`gemini-flash-lite-latest` alias, which resolves but changes the model under a column of scores
that claims to be comparable.

**First backfill, measured 2026-08-16** (1,742 distinct posting texts):

| | |
|---|---|
| Extraction | 1,742/1,742 succeeded, 200s |
| Scoring | 1,742 rows, 203s |
| `requirement_text` | 570 chars average, against 6,854 for the full posting — a 92% trim |
| Second incremental run | 0 rows, confirming the `content_hash` guard |
| BigQuery compute, all AI jobs | 0.131 GiB billed, 100.8 slot-minutes — under a cent |
| Out-of-range generations | 2 in 1,742 scored outside 1–5 |

**The dollar cost is not measured and cannot be from here.** Gemini token charges bill through
Vertex and never appear in `INFORMATION_SCHEMA.JOBS` — only the BigQuery compute above does — so
the authoritative figure is the billing console, the same conclusion
[ingestion-cost](../research/ingestion-cost.md) reached about storage rates. Measured input volume
is ~3.0M tokens per extraction pass and ~3.1M per scoring pass, which bounds it at sub-dollar even
at triple 0009's rate.
