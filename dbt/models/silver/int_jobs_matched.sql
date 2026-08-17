-- Embedding match: which piece of the resume does this posting most resemble?
--
-- Runs alongside the LLM's fit_score deliberately and TEMPORARILY. Both answer
-- the same question by different means and nothing yet shows which answers it
-- better, so both run until `make evaluate` compares them against human labels
-- and the loser is deleted.
--
-- Why embeddings can do this half and not the other: similarity is blind to
-- polarity. "We do not sponsor visas" and "we sponsor visas" are nearly
-- identical vectors, which is why geo_restriction and manages_people stay with
-- the LLM and never move here.
--
-- It is also not additive in cost: the LLM scorer re-sends the whole resume per
-- posting (~1,600 tokens x every row), while here the resume is embedded once
-- and each posting once, with the comparison itself arithmetic.
{{
    config(
        materialized='incremental',
        unique_key='content_hash',
        on_schema_change='append_new_columns',
    )
}}

{% if target.type == 'duckdb' or not var('enable_embeddings') %}

-- Stub: no embedding model on DuckDB, and none in prod until the remote model
-- exists. Same column shape, typed nulls, no rows.
--
-- `enable_embeddings` defaults to false so this model is inert on merge: the
-- remote model is one-time DDL over a CLOUD_RESOURCE connection (docs/v2-plan.md),
-- and referencing an object that does not exist yet would fail the whole
-- scheduled build for a feature nobody is using. Flip the var after the DDL.
--
-- Unlike the other two stubs this one can run on BigQuery, so it needs both
-- dialects' type names and a FROM: BigQuery rejects a WHERE on a query with
-- none, while `select ... where false` is fine on DuckDB.
    {%- set t_str = 'string' if target.type == 'bigquery' else 'varchar' %}
    {%- set t_float = 'float64' if target.type == 'bigquery' else 'double' %}
    select
        cast(null as {{ t_str }}) as content_hash,
        cast(null as {{ t_float }}) as similarity,
        cast(null as {{ t_str }}) as best_match_unit_id,
        cast(null as {{ t_str }}) as best_match_source,
        cast(null as {{ t_str }}) as best_match_evidences,
        cast(null as {{ t_str }}) as embedding_model,
        cast(null as timestamp) as matched_at
    from (select 1) _no_rows
    where false

{% else %}

with posting_vectors as (
    select
        content_hash,
        ml_generate_embedding_result as vec
    from
        ml.generate_embedding(
            model {{ var('embedding_model') }},
            (
                select
                    content_hash,
                    relevant_text as content
                from {{ ref('int_jobs_structured') }}
                where
                    extract_ok
                    and relevant_text is not null
                    and relevant_text != ''
                    {% if is_incremental() %}
                        and content_hash not in (select content_hash from {{ this }})
                    {% endif %}
            ),
            struct(true as flatten_json_output)
        )
),

-- The corpus, embedded. One row per resume bullet, so it is recomputed each run
-- rather than cached: a few vectors cost less than a staleness check.
resume_vectors as (
    select
        unit_id,
        source,
        evidences,
        ml_generate_embedding_result as vec
    from
        ml.generate_embedding(
            model {{ var('embedding_model') }},
            (
                select
                    unit_id,
                    source,
                    evidences,
                    text as content
                from {{ source('jobs_ops', 'resume_units') }}
            ),
            struct(true as flatten_json_output)
        )
),

-- Every posting against every bullet. The cross join is affordable because the
-- corpus is tiny and this is arithmetic over vectors, not model calls.
pairs as (
    select
        p.content_hash,
        r.unit_id,
        r.source,
        r.evidences,
        -- Cosine distance, so smaller is closer. Converted to a similarity
        -- below, so it rises with goodness like the fit_score beside it.
        ml.distance(p.vec, r.vec, 'COSINE') as distance
    from posting_vectors as p
    cross join resume_vectors as r
),

best as (
    select
        content_hash,
        unit_id,
        source,
        evidences,
        distance
    from pairs
    where true
    -- Best single bullet, not an average: averaging is the failure a
    -- bullet-level corpus exists to avoid (ADR-0027).
    qualify row_number() over (
        partition by content_hash order by distance asc, unit_id asc
    ) = 1
)

select
    content_hash,
    1 - distance as similarity,
    unit_id as best_match_unit_id,
    source as best_match_source,
    evidences as best_match_evidences,
    {{ var('embedding_model_name') }} as embedding_model,
    current_timestamp() as matched_at
from best

{% endif %}
