-- The resume, embedded: one vector per work bullet and one per industry worked
-- in. Rebuilt in full every run rather than cached. It is a few dozen vectors,
-- and a rebuild is what makes an edited or deleted bullet stop matching with no
-- staleness check to get wrong.
--
-- Same model and task type as the posting side, from the same vars: vectors
-- from different models or task types are not comparable, and int_jobs_matched
-- refuses to compare them.
{{ config(materialized='table') }}

{% if target.type == 'duckdb' or not var('enable_embeddings') %}

-- Stub: see int_posting_unit_vectors.
    {%- set t_str = 'string' if target.type == 'bigquery' else 'varchar' %}
    {%- set t_vec = 'array<float64>' if target.type == 'bigquery' else 'double[]' %}
    select
        cast(null as {{ t_str }}) as unit_id,
        cast(null as {{ t_str }}) as kind,
        cast(null as {{ t_str }}) as unit_source,
        cast(null as {{ t_str }}) as evidences,
        cast(null as {{ t_str }}) as resume_version,
        cast(null as {{ t_vec }}) as vec,
        cast(null as {{ t_str }}) as embedding_model,
        cast(null as {{ t_str }}) as task_type
    from (select 1) _no_rows
    where false

{% else %}

select
    unit_id,
    kind,
    unit_source,
    evidences,
    resume_version,
    ml_generate_embedding_result as vec,
    {{ var('embedding_model_name') }} as embedding_model,
    '{{ var("embedding_task_type") }}' as task_type
from
    ml.generate_embedding(
        model {{ var('embedding_model') }},
        (
            select
                unit_id,
                kind,
                source as unit_source,
                evidences,
                prompt_version as resume_version,
                text as content
            from {{ source('jobs_ops', 'resume_units') }}
        ),
        struct(
            true as flatten_json_output,
            '{{ var("embedding_task_type") }}' as task_type
        )
    )
where array_length(ml_generate_embedding_result) > 0

{% endif %}
