-- Dedup to the current row per posting, derive its lifecycle (is it still on the
-- board?), then drop hard deal-breakers using only structured + keyword signals
-- (no LLM in V1). Deal-breaker tech and the Canada location marker are seed-driven,
-- so the rules are data, not hardcoded SQL.
with lifecycle as (
    select
        *,
        -- when this posting was last seen on its board, across all ingests
        max(ingested_at) over (partition by job_key) as last_seen_at,
        -- the board's most recent ingest: a posting absent from it was taken down
        max(ingested_at) over (partition by source, company) as board_last_ingested_at
    from {{ ref('int_jobs__unioned') }}
),

deduped as (
    select *
    from lifecycle
    -- `where true` keeps BigQuery's parser happy: QUALIFY historically requires a
    -- WHERE / GROUP BY / HAVING clause alongside it.
    where true
    -- The raw landing is append-only, so "most recently ingested" is the current
    -- version of a posting. posted_or_updated_at can't order this: Lever only
    -- exposes createdAt, which never changes, so every re-ingest would tie.
    qualify row_number() over (
        partition by job_key
        order by ingested_at desc, posted_or_updated_at desc nulls last
    ) = 1
),

-- a posting is disqualified if its text contains ANY deal-breaker tech (word match)
tech_hits as (
    select d.job_key
    from deduped d
    cross join {{ ref('deal_breaker_tech') }} t
    where {{ regexp_word_ci('d.clean_text', 't.tech') }}
    group by d.job_key
),

-- Location rule (V1, deliberately coarse): keep a posting whose location is
-- unknown (null), is bare "Remote", or word-matches an allowed Canadian marker
-- (Canada / Ontario / ON / Ottawa / Toronto / Montreal — seeded). Word-matched,
-- not substring, so "ON" hits "Ottawa, ON" but not "London". No country blocklist.
-- So "Remote - United Kingdom" is dropped while "Remote, Canada" is kept; V2's
-- LLM does true location eligibility.
location_ok as (
    select d.job_key
    from deduped d
    left join {{ ref('allowed_locations') }} a
        on {{ regexp_word_ci('d.location', 'a.pattern') }}
    group by d.job_key
    having
        max(d.location) is null
        or lower(trim(max(d.location))) = 'remote'
        or count(a.pattern) > 0
)

select
    d.job_key,
    d.content_hash,
    d.source,
    d.company,
    d.external_id,
    d.title,
    d.location,
    d.remote_policy,
    d.department,
    d.employment_type,
    d.url,
    d.description_html,
    d.clean_text,
    d.posted_or_updated_at,
    d.ingested_at,
    d.last_seen_at,
    -- still on the board as of that board's latest ingest
    d.last_seen_at >= d.board_last_ingested_at as is_active
from deduped d
where
    d.job_key not in (select tech_hits.job_key from tech_hits)
    and d.job_key in (select location_ok.job_key from location_ok)
