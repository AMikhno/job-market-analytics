# 0018 — The company-discovery notebook lives in-repo, quarantined

**Status:** accepted

There is a Jupyter notebook that takes a list of companies (700+ Ottawa companies so far),
**detects each company's ATS** and writes a short description. It is run **manually, ~monthly or
less, on new companies only** — it is a discovery aid, not part of the scheduled pipeline.

ADR-0011 and ARCHITECTURE §4 stated V1 has "no automated discovery pipeline — there's no API for
it." This notebook is exactly that discovery step, now existing but **human-triggered**. It
populates the private company inventory that the ingest reads (`config/companies.csv`, ADR-0011),
and — with the openjobdata evaluation (ADR-0017) — it also classifies each company as
**openjobdata-covered vs needs custom collection**.

## Decision

Keep it **in-repo, quarantined**:

- **Location:** `tools/company_discovery/` — versioned alongside the pipeline it feeds, not in a
  separate repo, so the discovery logic and the ingest inventory evolve together.
- **Excluded from the CI gates.** The repo enforces `mypy --strict`, `ruff`, and an 85% pytest
  coverage gate; a notebook satisfies none of these. When the notebook is committed, exclude
  `tools/` from ruff/mypy/coverage (`pyproject.toml`) and from the pre-commit hooks
  (`.pre-commit-config.yaml`). *(Config change — a follow-up, not done at this documentation
  stage.)*
- **Real data stays private.** The notebook's **output** (the real company list / inventory) is
  gitignored and feeds `config/companies.csv` (ADR-0011). Only sanitized/example inputs may be
  committed. Company names + ATS are public info, but the curated target list is the user's and
  stays out of the public repo, like the company list itself.

## Why not the alternatives

- **Separate repo:** cleaner portfolio, but splits discovery from the inventory it feeds and makes
  the "which companies need custom collection" loop (ADR-0017) span two repos.
- **Port to a typed CLI module now:** the right *eventual* shape (a `tools/discover_ats.py` that
  passes the gates), but it is real code and premature — the notebook is run rarely and by hand.
  **Target future state:** convert to a gated CLI module once the discovery logic stabilizes.

## Consequence

ADR-0011 / ARCHITECTURE §4's "no discovery pipeline" wording is updated: discovery exists as this
**manual** notebook; it is deliberately not automated (no reliable discovery API), and its cadence
is monthly-or-less by hand.
