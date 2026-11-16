-- A posting, one sentence at a time: each requirement, nice-to-have and
-- responsibility the extraction listed, plus one sentence on the employer.
--
-- This is the unit the embedding matcher compares. A resume is a list of
-- bullets, so a posting embedded as one vector is compared against things a
-- tenth of its length: the posting's vector is an average of its topics, and
-- the nearest bullet is whichever one sits closest to that average. Sentence
-- against sentence is the comparison the corpus was split into bullets for
-- (ADR-0027).
--
-- A view: it only reshapes int_jobs_structured, and that table is the record.
--
-- unit_key hashes the text, not a position. Re-extracting a posting rewrites
-- its sentences, and a key built from position would hand the new sentence the
-- old sentence's stored vector.
{{ config(materialized='view') }}

{% if target.type == 'duckdb' %}

-- Dev stub: the lists exist only where extraction runs. Same columns, no rows.
    select
        cast(null as varchar) as unit_key,
        cast(null as varchar) as content_hash,
        cast(null as varchar) as kind,
        cast(null as varchar) as unit_text
    where false

{% else %}

with extracted as (
    select
        content_hash,
        requirements,
        nice_to_haves,
        responsibilities,
        company_summary
    from {{ ref('int_jobs_structured') }}
    where extract_ok
),

listed as (
    {%- for kind, column in [
        ('required', 'requirements'),
        ('nice_to_have', 'nice_to_haves'),
        ('responsibility', 'responsibilities'),
    ] %}
    select
        e.content_hash,
        '{{ kind }}' as kind,
        trim(item) as unit_text
    -- A null list unnests to no rows, which is what a posting with nothing of
    -- that kind should produce.
    from extracted e, unnest(json_value_array(e.{{ column }})) item
    union all
    {%- endfor %}
    select
        content_hash,
        'company' as kind,
        trim(company_summary) as unit_text
    from extracted
),

-- A sentence the model listed twice is one unit, and an item it placed in two
-- lists keeps the stronger claim: required over responsibility over optional.
ranked as (
    select
        content_hash,
        kind,
        unit_text,
        row_number() over (
            partition by content_hash, unit_text
            order by
                case kind
                    when 'company' then 0
                    when 'required' then 1
                    when 'responsibility' then 2
                    else 3
                end
        ) as claim
    from listed
    where unit_text is not null and unit_text != ''
)

select
    {{ dbt_utils.generate_surrogate_key(['content_hash', 'unit_text']) }} as unit_key,
    content_hash,
    kind,
    unit_text
from ranked
where claim = 1

{% endif %}
