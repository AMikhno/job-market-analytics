-- One row per posting across every tracked board: its latest landing plus its
-- lifecycle, before any filter.
--
-- The one place a run reads the whole description history. Hashing and stripping
-- text over every re-landing is what makes int_jobs__unioned expensive, so
-- anything that needs current text reads this table instead of repeating the
-- scan (ADR-0030). No location filter belongs here: the market models and the
-- posting history both read it, and neither may depend on private rules
-- (ADR-0028, ADR-0029).
with lifecycle as (
    select
        *,
        -- the earliest ingest that saw this posting: a job_key whose first_seen_at
        -- falls in the current run is net-new (the "new since last run" signal).
        min(ingested_at) over (partition by job_key) as first_seen_at,
        -- when this posting was last seen on its board, across all ingests
        max(ingested_at) over (partition by job_key) as last_seen_at,
        -- the board's most recent ingest: a posting absent from it was taken down
        max(ingested_at) over (partition by source, company) as board_last_ingested_at,
        -- the latest ingest across the whole pipeline: the staleness yardstick
        -- for boards that stopped being ingested at all (see is_active below)
        max(ingested_at) over () as pipeline_last_ingested_at
    from {{ ref('int_jobs__unioned') }}
),

latest as (
    select *
    from lifecycle
    -- `where true` keeps BigQuery's parser happy: QUALIFY historically requires a
    -- WHERE / GROUP BY / HAVING clause alongside it.
    where true
    -- The raw landing is append-only, so "most recently ingested" is the current
    -- version of a posting. posted_or_updated_at can't order this: Lever only
    -- exposes createdAt, which never changes, so every re-ingest would tie.
    --
    -- location is the final tie-break because one landing can hold several rows
    -- for one posting, one per city. Left arbitrary, the chosen city could differ
    -- between runs and the posting history would record a change nobody made.
    qualify row_number() over (
        partition by job_key
        order by ingested_at desc, posted_or_updated_at desc nulls last, location asc nulls last
    ) = 1
)

select
    job_key,
    content_hash,
    source,
    company,
    external_id,
    title,
    location,
    remote_policy,
    department,
    employment_type,
    url,
    description_html,
    clean_text,
    posted_or_updated_at,
    ingested_at,
    first_seen_at,
    last_seen_at,
    -- Active = still on the board as of that board's latest ingest, AND that
    -- board is itself still being ingested. Without the second clause, a board
    -- removed from the company list (or 404-ing forever - per-company failures
    -- only warn) freezes its board_last_ingested_at and its postings would stay
    -- "active" in gold indefinitely. The grace window tolerates ~2 consecutive
    -- failed runs at the twice-daily cadence (mirroring the 30h freshness gate)
    -- and self-heals: a recovering board's postings are re-seen and reactivate.
    (
        last_seen_at >= board_last_ingested_at
        and board_last_ingested_at >= {{ timestamp_hours_before(
            'pipeline_last_ingested_at', var('board_staleness_hours', 36)
        ) }}
    ) as is_active
from latest
