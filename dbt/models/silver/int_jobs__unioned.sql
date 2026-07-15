-- View, not ephemeral: dbt unit tests introspect input columns from the
-- warehouse, and an ephemeral model has no relation to introspect (ADR-0010).
{{ config(materialized='view') }}

-- Union every source into one shape, then derive the identity key, the
-- change-detection hash, and the cleaned text used by the keyword filter.
with unioned as (
    select * from {{ ref('stg_greenhouse__jobs') }}
    union all
    select * from {{ ref('stg_lever__jobs') }}
),

keyed as (
    select
        *,
        -- identity: one logical posting, stable across runs
        {{ dbt_utils.generate_surrogate_key(['source', 'company', 'external_id']) }} as job_key,
        -- cleaned, tag-stripped text for keyword matching
        {{ strip_html('description_html') }} as clean_text
    from unioned
)

select
    *,
    -- change detection: a new hash means the posting content changed -> reprocess in V2
    {{ dbt_utils.generate_surrogate_key(['title', 'clean_text']) }} as content_hash
from keyed
