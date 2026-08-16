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

**Cost is pending re-measurement.** 0009's ~$0.12 backfill + <$0.10/month came from 2.5
Flash-Lite's $0.05/$0.20 per 1M batch rate. That figure is not carried over on the assumption that
a same-named tier is priced the same; it is re-measured against the first backfill and recorded
then.
