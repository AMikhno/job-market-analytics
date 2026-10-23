-- What open data-role postings ask for: the share naming each technology.
--
-- The floor applies to the companies naming a technology, not to its posting
-- count: a term only one employer mentions would otherwise identify that
-- employer's stack, however many postings it has.
with open_data_roles as (
    select
        job_key,
        company
    from {{ ref('int_market__postings') }}
    where is_open and role_family != 'Other'
),

denominator as (
    select count(*) as open_data_postings from open_data_roles
)

select
    m.tech,
    m.category,
    count(distinct m.job_key) as postings,
    count(distinct r.company) as companies,
    cast(count(distinct m.job_key) as {{ dbt.type_float() }})
    / d.open_data_postings as share_of_postings
from {{ ref('int_market__tech_mentions') }} m
inner join open_data_roles r
    on m.job_key = r.job_key
cross join denominator d
group by m.tech, m.category, d.open_data_postings
having count(distinct r.company) >= {{ var('market_min_companies') }}
