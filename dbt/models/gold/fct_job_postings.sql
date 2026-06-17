-- The V1 deliverable: one row per posting, recency-ordered, with the link out.
-- (No fit score in V1 — relevance ranking arrives with the LLM in V2.)
select
    job_key,
    content_hash,
    source,
    company,
    title,
    location,
    remote_policy,
    url,
    posted_or_updated_at,
    ingested_at
from {{ ref('silver_jobs') }}
order by posted_or_updated_at desc nulls last
