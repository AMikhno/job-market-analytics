select
    source,
    company,
    external_id,
    title,
    location,
    remote_policy,
    department,
    employment_type,
    url,
    description_html,
    cast(posted_or_updated_at as timestamp) as posted_or_updated_at,
    cast(ingested_at as timestamp)   as ingested_at
from {{ source('jobs_raw', 'raw_lever_jobs') }}
