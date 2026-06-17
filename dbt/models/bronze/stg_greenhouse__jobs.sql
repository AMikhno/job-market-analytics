-- One model per source. Thin staging over the typed raw landing.
select
    source,
    company,
    external_id,
    title,
    location,
    cast(null as varchar)            as remote_policy,
    department,
    employment_type,
    url,
    description_html,
    cast(posted_or_updated_at as timestamp) as posted_or_updated_at,
    cast(ingested_at as timestamp)   as ingested_at
from {{ source('jobs_raw', 'raw_greenhouse_jobs') }}
