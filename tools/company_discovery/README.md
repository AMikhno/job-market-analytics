# Company discovery tools

**Manual, on-demand tooling — NOT part of the V1 ingestion pipeline.** These build and
enrich the private company list that feeds `config/companies.csv` /
`COMPANIES_CSV_CONTENT`. They are run rarely, by hand, and are intentionally fenced off
from the repo's gates (own deps, no `mypy --strict`/coverage requirement). Nothing here
runs in CI or in the scheduled ingest.

Two decoupled stages — run them **independently**, never as one job:

## Stage 1 — ATS / careers discovery  (`ats_audit.py`)
Pure browsing, **no LLM, no cloud credits.** Renders each company's own site with
Playwright, finds its careers page (DOM link → sitemap → path fallback), dismisses
cookie-consent, and reads the ATS the page actually calls (network requests + DOM).
Anchored to the company's own domain, so no name-collision false positives; rendered, so
JS-injected boards are visible.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

python ats_audit.py --xlsx "path/to/companies.xlsx" --sheet "Company List"
# outputs (next to defaults in ~/Downloads, override with --out / --ingestable):
#   ats_audit_results.csv     full audit: careers URL, ATS, board token, found-via, status
#   companies_ingestable.csv  GH/Lever/Ashby rows in the config/companies.csv schema
```
Resume-safe: re-running skips companies already in the output CSV. Checkpoints every
`--checkpoint-every` companies.

**Caveats:** directory/portal rows (e.g. REDACTED, REDACTED) attribute a member
company's board to themselves — drop them. `Found Via = none` rows are bot-blocked or
consent-walled and stayed unreachable (an honest Unknown bucket, not silent wrong data).

## Stage 2 — analytics categorization  (`categorize.ipynb`)
**Uses Gemini via Vertex AI — draws Google Cloud credits.** Runs *after* Stage 1. Scores
whether each company would structurally employ data/analytics staff, using grounded web
search. Lives as a Colab notebook (auth via Colab ADC → Vertex, no API key). Export a
**redacted** copy here from Colab (File → Download `.ipynb`) — strip outputs and confirm
no sheet ID / real company data is baked in before committing.

**Handoff from Stage 1:** import Stage 1's `ats_audit_results.csv` into a worksheet named
`ATS_Audit` in the source Google Sheet. The notebook reads each company's career page +
detected ATS from that tab (`AUDIT_SHEET`) and writes its analytics scores to `Results`.

## Secrets / privacy
No secret values in these files. Stage 1 takes the xlsx path as an argument. Stage 2
reads the source-sheet URL from Colab Secrets and authenticates to Vertex via ADC (no
key). The real company list and any captured board data are **not** committed here.
