-- Open postings per role family, with how each family states its work location.
-- "Other" is every posting outside the data families -- kept, so the data
-- families can be read as a share of the whole board population.
select
    role_family,
    count(*) as open_postings,
    count(distinct company) as companies,
    sum(case when remote_policy = 'remote' then 1 else 0 end) as remote_postings,
    sum(case when remote_policy = 'hybrid' then 1 else 0 end) as hybrid_postings,
    sum(case when remote_policy = 'onsite' then 1 else 0 end) as onsite_postings,
    sum(case when remote_policy = 'unstated' then 1 else 0 end) as unstated_postings
from {{ ref('int_market__postings') }}
where is_open
group by role_family
having count(distinct company) >= {{ var('market_min_companies') }}
