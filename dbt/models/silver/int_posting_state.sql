-- What the posting history tracks, and nothing else. A snapshot stores every
-- column of its relation in every version, so the description stays out: the
-- content_hash already records that the text changed (ADR-0030).
{{ config(materialized='view') }}

select
    job_key,
    source,
    company,
    title,
    location,
    remote_policy,
    content_hash,
    is_active
from {{ ref('int_jobs__latest') }}
