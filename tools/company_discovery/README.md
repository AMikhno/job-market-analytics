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
# from the repo root — deps are ephemeral, nothing installs into the project env
make discover XLSX=~/Downloads/new_candidates.xlsx
playwright install chromium   # once, if the browser is missing
```

**Everything durable lands in `config/discovery/` (gitignored), never in Downloads.**
Only the *input* xlsx of new candidates comes from outside the repo:

```
config/discovery/
  ats_audit_results.csv       the durable cache: every company + website + careers URL +
                              detected ATS + board token + status. Re-runs resume from it,
                              so a new candidate list only renders the rows it hasn't seen.
  companies_ingestable.csv    the GH/Lever/Ashby rows (config/companies.csv schema)
  companies_inventory.csv     every detected-ATS company: V1 active=true + inventory
                              active=false (ADR-0013)
```

Then `make update-company-list` stages the inventory into `config/companies.csv` (backing
up the previous master) and writes the active-only CI projection.

**How it finds a board**, in order — each step exists because the one before it missed real
companies:
1. careers link from the DOM → sitemap → `/careers` path;
2. network requests + DOM `src`/`href` on that page;
3. **deeper hop** into board-like links, because a `/careers` landing page is often pure
   marketing with the board one click further on;
4. **raw-HTML scan**, because some sites embed the ATS host in a JSON payload rather than
   any element attribute;
5. **API probe** of **every** V1 endpoint (nine today) with name/domain-derived tokens, because
   some companies proxy their board server-side and never name the ATS in the page at all.
   Keep this list in step with `ingest/sources.py`: while the probe knew only
   Greenhouse/Lever/Ashby, five companies sat in the master with a blank or wrong ref although
   their board answered on the first token a human would guess.

A company whose ATS is detected but whose **board token cannot be extracted** goes through the
API probe too — a row that names a platform but has nothing to fetch is worse than an honest
Unknown, because it looks classified and can never be activated.

**Caveats:** directory/portal rows — tech-hub sites and regional ecosystem portals, which are not
employers — attribute a *member* company's board to themselves; drop them. This is not rare and it
is not a bug you can fix by re-running: a re-audit reproduced the same wrong board ref from two
unrelated portal sites, because that is genuinely what those pages embed. `Found Via =
none` rows are bot-blocked or consent-walled and stayed unreachable (an honest Unknown bucket, not
silent wrong data). The API probe only tries tokens a human would guess, so a miss is never proof a
company has no board — a name with a corporate suffix, or with digits appended, is unreachable that
way.

## Stage 2 — analytics categorization  (`categorize.ipynb`)
**Uses Gemini via Vertex AI — draws Google Cloud credits.** Runs *after* Stage 1. Scores
whether each company would structurally employ data/analytics staff, using grounded web
search. Lives as a Colab notebook (auth via Colab ADC → Vertex, no API key). Export a
**redacted** copy here from Colab (File → Download `.ipynb`) — strip outputs and confirm
no sheet ID / real company data is baked in before committing.

**Handoff from Stage 1:** import Stage 1's `ats_audit_results.csv` into a worksheet named
`ATS_Audit` in the source Google Sheet. The notebook reads each company's career page +
detected ATS from that tab (`AUDIT_SHEET`) and writes its analytics scores to `Results`.

## Refreshing the company list (recurring)
Adding companies is incremental — the cache in `config/discovery/` means a refresh only
renders rows it has never seen:

1. Put the new candidates (Company Name + Website) in an `.xlsx`.
2. `make discover XLSX=~/Downloads/new_candidates.xlsx`
3. `make update-company-list` — backs up the old master, stages the inventory, validates,
   and writes `config/companies.active.csv`.
4. Push it (human-authenticated):
   `gh variable set COMPANIES_CSV_CONTENT < config/companies.active.csv`

**The variable gets the active rows only**, not the whole master: the pipeline reads
nothing else, GitHub caps a variable at 48 KB, and the inventory is the fastest-growing
part of the list. Columns are identical, so the projection validates like the master and
doubles as a backup of the active set.

`config/companies.csv` and `config/discovery/` are gitignored — the real list never enters
the repo; it lives in your working tree and (the active slice of it) the Actions variable.

## Secrets / privacy
No secret values in these files. Stage 1 takes the xlsx path as an argument. Stage 2
reads the source-sheet URL from Colab Secrets and authenticates to Vertex via ADC (no
key). The real company list and any captured board data are **not** committed here.
