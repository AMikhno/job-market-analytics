-- Workday lands a posting from its list row alone once the posting's
-- description is already in the warehouse (ADR-0030), so those rows carry no
-- detail fields. Fill them from the posting's latest described landing, so
-- silver -- which keeps each posting's latest row -- always reads a full one.
-- Title and ingested_at stay the row's own: the list is current, and the
-- lifecycle is built from every landing.
with staged as (
    {{ stage_raw_jobs('raw_workday_jobs') }}
),

described as (
    select
        company,
        external_id,
        location,
        remote_policy,
        employment_type,
        url,
        description_html,
        posted_or_updated_at
    from staged
    where coalesce(description_html, '') != ''
    qualify row_number() over (
        partition by company, external_id
        order by ingested_at desc
    ) = 1
)

select
    s.source,
    s.company,
    s.external_id,
    s.title,
    -- `location` is the shared staging column name the union depends on.
    coalesce(d.location, s.location) as location,  -- noqa: RF04
    coalesce(d.remote_policy, s.remote_policy) as remote_policy,
    s.department,
    coalesce(d.employment_type, s.employment_type) as employment_type,
    coalesce(d.url, s.url) as url,
    coalesce(d.description_html, s.description_html) as description_html,
    coalesce(d.posted_or_updated_at, s.posted_or_updated_at) as posted_or_updated_at,
    s.ingested_at
from staged s
left join described d
    on
        s.company = d.company
        and s.external_id = d.external_id
