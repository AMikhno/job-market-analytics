# 0011 — The company list is private config, not a committed seed

**Status:** accepted

The curated company list is the user's ingestion target and is kept **out of the public repo**,
like `.env`. The real list lives in `config/companies.csv` (gitignored); only
`config/companies.example.csv` is committed (for format + CI). The Python ingest reads
`settings.companies_csv` (default `config/companies.csv`), falling back to the example when the
private file is absent, so a fresh clone and CI still run.

It is no longer a dbt seed: no model referenced it, and seeding it would have published it. The
deal-breaker tech and allowed-location lists remain committed seeds (generic, non-private, and used
by models). If a warehouse-side company table is wanted in V2, ingest can land it then.

The private list is populated by the manual company-discovery notebook (ADR-0018), which detects
each company's ATS; that notebook's real-company output is gitignored for the same reason this list
is.
