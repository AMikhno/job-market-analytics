# 0025 — Gemini 3.1 Flash-Lite, called on the global endpoint

**Status:** accepted (for V2; supersedes 0009)

ADR-0009 chose Gemini 2.5 Flash-Lite and avoided 2.0 Flash because 2.0 was being retired. The 2.5
family now retires in turn (shutdown 2026-10-20), so 0009's own argument applies to the model it
selected. Its reasoning is unchanged — extraction and scoring are simple structured tasks, so the
cheapest capable tier wins — and only the id moves: **`gemini-3.1-flash-lite`**.

**The model has no regional endpoint.** One `generateContent` call per endpoint, measured
2026-08-15:

| Endpoint | Result |
|---|---|
| `global` | 200, `modelVersion: gemini-3.1-flash-lite` |
| `northamerica-northeast2` | 400 `FAILED_PRECONDITION` |
| `northamerica-northeast1` | 404 `NOT_FOUND` |
| `us-central1` | 404 `NOT_FOUND` |

No foundational Gemini model runs natively in `northamerica-northeast2`, which is the warehouse
location (`shared/config.py`). Google's in-region Canadian processing for Gemini is in
`northamerica-northeast1`, which does not serve this model either — so the choice is not between
two Canadian regions, it is between the global endpoint and no model at all. Global is acceptable
here: the pipeline reads public job postings and has no data-residency requirement.

This matters for V2 because BigQuery requires an AI connection to sit in its dataset's location.
Whether that blocks in-SQL inference from a `northamerica-northeast2` dataset is **not yet
measured**; `AI.SCORE` is natively managed and provisions no connection, which would make the
question moot. `docs/v2-plan.md` gates the build on that measurement rather than assuming either
answer.

**BigQuery's supported endpoints are not Vertex's**, and this is the practical constraint —
the table above describes what Vertex serves, which turns out to be a poor guide to what the
in-SQL functions can call. `us-central1` returns 404 for this model at the raw publisher
endpoint, yet `AI.GENERATE` reaches it from a `us-central1` dataset without trouble: BigQuery
resolves through its own path against its own allowlist. Probed by calling `AI.GENERATE` with each
endpoint, 2026-08-16:

| Endpoint | BigQuery |
|---|---|
| `gemini-3.1-flash-lite` | works — the newest *flash-lite* it accepts |
| `gemini-3.5-flash` | works (newer, but the flash tier) |
| `gemini-3.5-flash-lite` | `Unsupported endpoint` |
| `gemini-3.1-flash` | `Unsupported endpoint` |

The allowlist is non-contiguous — lite at 3.1, flash at 3.5, neither the other way round — so
"newest available on Vertex" is the wrong question to ask. Probe BigQuery directly before changing
`scoring_endpoint`, and prefer an explicit version over the `gemini-flash-lite-latest` alias, which
resolves but changes the model under a column of scores that claims to be comparable.

**First backfill, measured 2026-08-16** (1,742 distinct posting texts):

| | |
|---|---|
| Extraction | 1,742/1,742 succeeded, 200s |
| Scoring | 1,742 rows, 203s |
| `requirement_text` | 570 chars average, against 6,854 for the full posting — a 92% trim |
| Second incremental run | 0 rows, confirming the `content_hash` guard |
| BigQuery compute, all AI jobs | 0.131 GiB billed, 100.8 slot-minutes — under a cent |
| Out-of-range generations | 2 in 1,742 scored outside 1–5 |

**The dollar cost is still not measured, and cannot be from here.** 0009's ~$0.12 came from 2.5
Flash-Lite's $0.05/$0.20 per 1M rate, which is not carried over. Gemini token charges bill through
Vertex and do not appear in `INFORMATION_SCHEMA.JOBS` — only the BigQuery compute above does — so
the authoritative figure is the project's billing console, the same conclusion
[ingestion-cost](../research/ingestion-cost.md) reached about storage rates. Measured input volume
is ~3.0M tokens per extraction pass and ~3.1M per scoring pass, which bounds it: even at triple the
old rate this is a sub-dollar backfill.
