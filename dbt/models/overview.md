{% docs __overview__ %}
# job-market-analytics — lineage

The dbt half of a scheduled pipeline over public job-board feeds. Python lands each ATS's postings
into its own append-only raw table; this project models them through three zones into one table of
live, deduplicated, ranked postings, and an email digest reads from that.

**Back to the [project site](https://amikhno.github.io/job-market-analytics/)** ·
[architecture](https://amikhno.github.io/job-market-analytics/docs/architecture/) ·
[decision records](https://amikhno.github.io/job-market-analytics/docs/decisions/) ·
[source](https://github.com/AMikhno/job-market-analytics)

## How to read it

Open the graph with the lineage button at the bottom right, or start from a model in the sidebar.

| Zone | Models | Does |
|---|---|---|
| raw | `raw_*_jobs` sources | One append-only landing table per ATS, written by the Python ingest |
| bronze | `stg_*__jobs` | Cast and standardize one source each (views) |
| silver | `int_jobs__unioned`, `silver_jobs`, `int_jobs_*` | Union, identity and change hashes, dedup, lifecycle, the location filter and soft ranking signals; the V2 AI models |
| gold | `fct_job_postings` | One row per *live* posting, with its link and ranking columns |
| exposure | `email_digest` | Postings first seen since the last digest, ordered by score |

## What this build is

Generated in CI against the **DuckDB** dev target, which needs no credentials. Production runs the
same models on BigQuery. The three V2 models (`int_jobs_structured`, `int_jobs_scored`,
`int_jobs_matched`) call BigQuery's in-SQL AI functions, so here they appear as typed stubs with the
production column shape and no upstream edges. Gold joins them `LEFT`, so an unscored posting
still ships — on any target.

The filter-rule seeds are built from the committed examples, not the production rules, which are
private configuration.
{% enddocs %}
