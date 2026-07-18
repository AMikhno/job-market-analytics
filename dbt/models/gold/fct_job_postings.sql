-- The V1 deliverable: one row per posting that is still live on its board,
-- with a recency rank and the link out. (No fit score in V1 — relevance
-- ranking arrives with the LLM in V2.)
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
    ingested_at,
    first_seen_at,
    last_seen_at,
    desired_tech_hits,
    title_match,
    row_number() over (
        order by posted_or_updated_at desc nulls last, job_key asc
    ) as recency_rank
from {{ ref('silver_jobs') }}
-- postings that disappeared from their board are closed — not deliverable
where is_active
