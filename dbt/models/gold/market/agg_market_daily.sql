-- Open and newly posted postings per observed day, across every tracked board.
--
-- The days are the days the pipeline ran, not a calendar spine: a day with no
-- run has no observation, and drawing it as zero would invent a dip.
--
-- "New" excludes left-censored postings. On a board's first ingest every
-- posting is seen for the first time, and counting those as new would put a
-- spike on each board's first day that is the pipeline's history, not the market's.
with postings as (
    select * from {{ ref('int_market__postings') }}
),

days as (
    select distinct cast(ingested_at as date) as observed_on
    from {{ ref('int_jobs__unioned') }}
),

daily as (
    select
        d.observed_on,
        count(*) as open_postings,
        sum(
            case
                when cast(p.first_seen_at as date) = d.observed_on and not p.is_left_censored then 1
                else 0
            end
        ) as new_postings,
        count(distinct p.company) as companies
    from days d
    inner join postings p
        on
            cast(p.first_seen_at as date) <= d.observed_on
            and cast(p.last_seen_at as date) >= d.observed_on
    group by d.observed_on
)

select
    observed_on,
    open_postings,
    new_postings,
    companies
from daily
where companies >= {{ var('market_min_companies') }}
