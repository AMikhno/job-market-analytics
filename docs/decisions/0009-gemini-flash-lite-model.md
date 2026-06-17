# 0009 — Use Gemini 2.5 Flash-Lite for V2 extraction/scoring

**Status:** accepted (for V2)

Extraction and scoring are simple structured tasks, so the cheapest capable model wins. Gemini 2.5
Flash-Lite ($0.05/$0.20 per 1M at the batch rate in-SQL AI uses) keeps V2 to ~$0.12 backfill +
<$0.10/month at ~100 companies. Gemini 2.0 Flash is being retired (2026-06-01), so it is avoided.
Re-route only genuinely hard cases to a Flash/Pro tier later if scoring quality demands it.
