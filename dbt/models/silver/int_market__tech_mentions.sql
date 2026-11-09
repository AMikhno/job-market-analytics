-- Which vocabulary terms each open data-role posting names: one row per
-- (posting, label). Only open postings in a data role family, and only their
-- latest text, because the chart answers "what do data roles ask for now".
--
-- A posting counts once per label however many of the label's patterns it
-- names (PostgreSQL and Postgres are one technology).
with open_data_roles as (
    select job_key
    from {{ ref('int_market__postings') }}
    where is_open and role_family != 'Other'
),

latest_text as (
    select
        l.job_key,
        l.clean_text
    from {{ ref('int_jobs__latest') }} l
    inner join open_data_roles o
        on l.job_key = o.job_key
)

select distinct
    t.job_key,
    v.label as tech,
    v.category
from latest_text t
inner join {{ ref('market_tech_vocabulary') }} v
    on {{ regexp_word_ci('t.clean_text', 'v.pattern') }}
