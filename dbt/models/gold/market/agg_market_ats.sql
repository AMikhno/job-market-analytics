-- Open postings by applicant-tracking system. An ATS with fewer boards than the
-- publishing floor folds into "other" rather than being dropped, so the total
-- still adds up -- and "other" itself is published only if it clears the floor.
with by_source as (
    select
        source,
        count(*) as open_postings,
        count(distinct company) as boards
    from {{ ref('int_market__postings') }}
    where is_open
    group by source
),

folded as (
    select
        case when boards >= {{ var('market_min_companies') }} then source else 'other' end as ats,
        open_postings,
        boards
    from by_source
)

select
    ats,
    sum(open_postings) as open_postings,
    sum(boards) as boards
from folded
group by ats
having sum(boards) >= {{ var('market_min_companies') }}
