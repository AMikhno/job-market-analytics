# 0003 — AI deferred to V2; V1 is ingestion + dbt only

**Status:** accepted

V1 ships a trustworthy, deduplicated, rule-filtered dataset built entirely from ingestion
and SQL. LLM structuring/scoring and embeddings are V2. Consequence: V1's deal-breaker
filter can use structured fields and keyword matches (e.g. Kafka/Spark) but cannot judge
required-vs-nice-to-have or infer seniority — that needs the LLM and waits for V2.
