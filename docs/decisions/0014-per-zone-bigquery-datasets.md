# 0014 — Each medallion zone lands in its own BigQuery dataset

**Status:** accepted

The Python ingest already isolates its landing and run metadata into the `jobs_raw` and
`jobs_ops` datasets (`shared/storage.py`). The dbt models, by contrast, all built into a
single `jobs` dataset, mixing staging views, intermediate tables, and the delivered mart in
one namespace.

Each zone now lands in its own dataset — `jobs_bronze`, `jobs_silver`, `jobs_gold` — so the
warehouse layout mirrors the medallion structure, access can be granted per zone (e.g. read
on gold only, without exposing raw), and the delivered mart is unambiguous.

This needs **no custom macro**. dbt's default `generate_schema_name` concatenates
`<target dataset>_<custom schema>`, so a `+schema: bronze|silver|gold` under each model
folder in `dbt_project.yml` yields:

- `jobs_bronze` / `jobs_silver` / `jobs_gold` on BigQuery — matching the existing
  `jobs_raw` / `jobs_ops` names; and
- `main_bronze` / `main_silver` / `main_gold` on DuckDB dev — keeping dual-target parity, so
  CI (which builds on DuckDB) exercises the same schema split that prod uses.

The raw *source* dataset (`jobs_raw`, created by the Python ingest, read by dbt sources) is
unchanged. The bare `jobs` dataset is no longer used by dbt.
