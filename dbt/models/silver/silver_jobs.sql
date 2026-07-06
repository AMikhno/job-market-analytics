-- Dedup to the latest row per posting, then drop hard deal-breakers using only
-- structured + keyword signals (no LLM in V1). Deal-breaker tech and allowed
-- locations are seed-driven, so the rules are data, not hardcoded SQL.
with deduped as (
    select *
    from {{ ref('int_jobs__unioned') }}
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

-- a posting passes the location gate if it matches ANY allowed pattern (or is null)
location_ok as (
    select d.job_key
    from deduped d
    left join {{ ref('allowed_locations') }} a
        on lower(coalesce(d.location, '')) like '%' || lower(a.pattern) || '%'
    group by d.job_key
    having count(a.pattern) > 0 or max(d.location) is null
)

select d.*
from deduped d
where d.job_key not in (select job_key from tech_hits)
  and d.job_key in (select job_key from location_ok)
