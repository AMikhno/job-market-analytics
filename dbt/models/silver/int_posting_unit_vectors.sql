-- One embedding per posting sentence (int_posting_units). The only part of the
-- embedding matcher that calls a model per posting, so the only incremental
-- part.
--
-- A stored vector stays valid while three things hold: the sentence, the
-- embedding model and the task type. The sentence is in the key. The other two
-- are recorded on each row and compared in the guard, so changing either var
-- re-embeds instead of leaving two incomparable kinds of vector in one column.
--
-- The resume is deliberately NOT one of the three. A posting's vector does not
-- depend on who it is compared with, so editing the resume costs no call here:
-- int_jobs_matched recomputes every comparison from these vectors each run.
--
-- Vectors for sentences a re-extraction replaced are left behind and ignored:
-- int_jobs_matched joins back to the current units.
{{
    config(
        materialized='incremental',
        unique_key='unit_key',
        on_schema_change='append_new_columns',
    )
}}

{% if target.type == 'duckdb' or not var('enable_embeddings') %}

-- Stub: no embedding model on DuckDB, and none in prod until the remote model
-- exists (see int_jobs_matched). Runs on BigQuery too while the flag is off,
-- so it needs both dialects' type names and a FROM.
    {%- set t_str = 'string' if target.type == 'bigquery' else 'varchar' %}
    {%- set t_vec = 'array<float64>' if target.type == 'bigquery' else 'double[]' %}
    select
        cast(null as {{ t_str }}) as unit_key,
        cast(null as {{ t_str }}) as content_hash,
        cast(null as {{ t_vec }}) as vec,
        cast(null as {{ t_str }}) as embedding_model,
        cast(null as {{ t_str }}) as task_type,
        cast(null as timestamp) as embedded_at
    from (select 1) _no_rows
    where false

{% else %}

with embedded as (
    select
        unit_key,
        content_hash,
        ml_generate_embedding_result as vec
    from
        ml.generate_embedding(
            model {{ var('embedding_model') }},
            (
                select
                    unit_key,
                    content_hash,
                    unit_text as content
                from {{ ref('int_posting_units') }}
                {% if is_incremental() %}
                    where unit_key not in (
                        select unit_key
                        from {{ this }}
                        where
                            embedding_model = {{ var('embedding_model_name') }}
                            and task_type = '{{ var("embedding_task_type") }}'
                    )
                {% endif %}
            ),
            -- The task type is stated, never left to the default: a default is
            -- the provider's to change, and it would change every vector here
            -- without changing a line of this file.
            struct(
                true as flatten_json_output,
                '{{ var("embedding_task_type") }}' as task_type
            )
        )
)

select
    unit_key,
    content_hash,
    vec,
    {{ var('embedding_model_name') }} as embedding_model,
    '{{ var("embedding_task_type") }}' as task_type,
    current_timestamp() as embedded_at
from embedded
-- A failed call returns an empty vector. Not stored, so the guard above sees no
-- row for it and the next run tries again.
where array_length(vec) > 0

{% endif %}
