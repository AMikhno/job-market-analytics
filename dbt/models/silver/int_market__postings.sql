-- One row per posting across every tracked board, for the site's market charts.
--
-- Reads the union, not silver_jobs, on purpose: silver applies the location
-- filter, and the allowed locations are private config (ADR-0028). An aggregate
-- of a location-filtered table would publish where the filter points. Upstream
-- of the filter, the charts describe the boards, not the search (ADR-0029).
--
-- The grain is the posting, not the landing, so the lifecycle here is a group-by
-- rather than silver's window over every re-landing.
with landings as (
    select
        job_key,
        source,
        company,
        title,
        remote_policy,
        ingested_at
    from {{ ref('int_jobs__unioned') }}
),

boards as (
    select
        source,
        company,
        min(ingested_at) as board_first_ingested_at,
        max(ingested_at) as board_last_ingested_at
    from landings
    group by source, company
),

pipeline as (
    select max(ingested_at) as pipeline_last_ingested_at
    from landings
),

postings as (
    select
        job_key,
        source,
        company,
        min(ingested_at) as first_seen_at,
        max(ingested_at) as last_seen_at
    from landings
    group by job_key, source, company
),

latest as (
    select
        job_key,
        title,
        remote_policy
    from landings
    where true
    qualify row_number() over (partition by job_key order by ingested_at desc) = 1
),

families as (
    select
        l.job_key,
        f.family
    from latest l
    inner join {{ ref('market_title_families') }} f
        on {{ regexp_word_ci('l.title', 'f.pattern') }}
    where true
    qualify row_number() over (partition by l.job_key order by f.priority, f.family) = 1
)

select
    p.job_key,
    p.source,
    p.company,
    coalesce(f.family, 'Other') as role_family,
    -- Boards spell this field every way an ATS can ("Remote", "remote",
    -- "Fully remote", "OnSite", "On-site"); most leave it empty.
    case
        when lower(l.remote_policy) like '%remote%' then 'remote'
        when lower(l.remote_policy) like '%hybrid%' then 'hybrid'
        when lower(replace(replace(l.remote_policy, '-', ''), ' ', '')) like '%onsite%'
            then 'onsite'
        else 'unstated'
    end as remote_policy,
    p.first_seen_at,
    p.last_seen_at,
    -- Gone from its board while the board was still being read: an observed close.
    p.last_seen_at < b.board_last_ingested_at as is_closed,
    -- Same rule as silver_jobs.is_active: still on a board that is still ingested.
    (
        p.last_seen_at >= b.board_last_ingested_at
        and b.board_last_ingested_at >= {{ timestamp_hours_before(
            'pl.pipeline_last_ingested_at', var('board_staleness_hours', 36)
        ) }}
    ) as is_open,
    -- Already up when its board was first read, so its real start is unknown.
    -- Its duration would be understated, and its first sighting is not "new".
    p.first_seen_at = b.board_first_ingested_at as is_left_censored
from postings p
inner join boards b
    on p.source = b.source and p.company = b.company
inner join latest l
    on p.job_key = l.job_key
cross join pipeline pl
left join families f
    on p.job_key = f.job_key
