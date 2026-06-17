# 0004 — V2 LLM runs inside dbt as SQL (Pattern C)

**Status:** accepted (for V2)

LLM extraction/scoring will run as dbt SQL models using BigQuery AI functions
(`AI.GENERATE`, `AI.GENERATE_INT`, `AI.EMBED`). Rejected: a Python pre-step (sees bronze,
before silver's dedup/filter — would label everything) and dbt Python models (diverge
across DuckDB/BigQuery). Reading from silver means the LLM only sees survivors; in-SQL
inference bills at the batch rate.
