-- Open and newly posted postings per observed day, across every tracked board.
--
-- The days are the days the pipeline ran, not a calendar spine: a day with no
-- run has no observation, and drawing it as zero would invent a dip.
--
-- "New" excludes left-censored postings. On a board's first ingest every
-- posting is seen for the first time, and counting those as new would put a
-- spike on each board's first day that is the pipeline's history, not the market's.
--
-- `boards` is how many boards were read that day. The board list grows, and
-- open_postings jumps every time it does; divided by boards, the series shows
-- hiring at the tracked companies rather than the history of the list.
with postings as (
    select * from {{ ref('int_market__postings') }}
),

boards_read as (
    select
        cast(ingested_at as date) as observed_on,
        count(distinct source || '/' || company) as boards
    from {{ ref('int_jobs__unioned') }}
    group by cast(ingested_at as date)
),

daily as (
    select
        d.observed_on,
        d.boards,
        count(*) as open_postings,
        sum(
            case
                when cast(p.first_seen_at as date) = d.observed_on and not p.is_left_censored then 1
                else 0
            end
        ) as new_postings,
        count(distinct p.company) as companies
    from boards_read d
    inner join postings p
        on
            cast(p.first_seen_at as date) <= d.observed_on
            and cast(p.last_seen_at as date) >= d.observed_on
    group by d.observed_on, d.boards
)

select
    observed_on,
    boards,
    open_postings,
    new_postings,
    companies
from daily
where companies >= {{ var('market_min_companies') }}
