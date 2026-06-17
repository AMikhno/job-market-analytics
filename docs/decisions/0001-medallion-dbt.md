# 0001 — Medallion layers in one dbt project

**Status:** accepted

Bronze (per-source staging) → silver (union, dedup, rule-filter) → gold (curated
deliverable). One dbt project owns the whole transform DAG so lineage and tests are in
one place. Both ingestion sources feed the same project.
