{# Bronze staging for one ATS: select the common columns off its raw landing
   and cast the two timestamps. Every source's staging model is identical
   apart from the table it reads, and with nine of them the copies were the
   thing most likely to drift (a column added to RAW_COLUMNS reaching some
   staging models and not others). The column list lives here once instead.

   Raw is all-STRING by design (shared/storage.py); bronze owns the casts. #}

{% macro stage_raw_jobs(raw_table) -%}
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
    cast(ingested_at as timestamp) as ingested_at
from {{ source('jobs_raw', raw_table) }}
{%- endmacro %}
