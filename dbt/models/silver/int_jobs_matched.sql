-- Embedding match: which piece of the resume does this posting most resemble?
--
-- This is the similarity half of V2, and it exists alongside the LLM's
-- fit_score deliberately and temporarily. The two answer the same question by
-- different means, and nothing yet shows which answers it better -- so both run
-- until `make evaluate` compares them against human labels, and then the loser
-- is deleted. Carrying both forever would be the failure mode; carrying both
-- for one evaluation is the only way to choose.
--
-- Why embeddings can do this half and not the other: similarity is blind to
-- polarity. "We do not sponsor visas" and "we sponsor visas" are nearly
-- identical vectors, which is why geo_restriction and manages_people stay with
-- the LLM (int_jobs_structured) and never move here.
--
-- Cost shape, and the reason this is not simply additive: the LLM scorer re-sends
-- the entire resume with every posting (~1,600 tokens x every row). Here the
-- resume is embedded once -- a handful of vectors, reused forever -- and each
-- posting once, with the comparison itself being arithmetic. Same question,
-- roughly a fiftieth of the tokens.
{{
    config(
        materialized='incremental',
        unique_key='content_hash',
        on_schema_change='append_new_columns',
    )
}}

{% if target.type == 'duckdb' or not var('enable_embeddings') %}

-- Stub: no embedding model on DuckDB, and none in prod until the remote model
-- exists. Same column shape, typed nulls, no rows, so gold compiles and its
-- null-path tests run unchanged.
--
-- `enable_embeddings` defaults to false so this model is inert on merge. The
-- remote model is one-time DDL over a CLOUD_RESOURCE connection (see
-- docs/v2-plan.md), and a model referencing an object that does not exist yet
-- would fail the whole scheduled build -- taking ingestion and delivery down
-- with it for a feature that is not being used. Flip the var once the DDL has
-- run.
--
-- Unlike the other two stubs, this one can run on BigQuery -- they are reached
-- only on the dev target, this one whenever embeddings are off. So it needs
-- both dialects' type names, and a FROM: BigQuery rejects a WHERE clause on a
-- query that has none, while `select ... where false` is fine on DuckDB.
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

-- The corpus, embedded. Small by construction -- one row per resume bullet --
-- so this is recomputed each run rather than cached: a few vectors cost less
-- than the machinery to decide whether they are stale.
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

-- Every posting against every bullet. The cross join is affordable precisely
-- because the corpus is tiny; it is arithmetic over vectors, not model calls.
pairs as (
    select
        p.content_hash,
        r.unit_id,
        r.source,
        r.evidences,
        -- Cosine distance, so smaller is closer. Converted to a similarity
        -- below, because a number that goes up with goodness is the one that
        -- can sit beside fit_score without confusing the reader.
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
    -- Best single bullet, not an average over all of them: averaging is exactly
    -- the failure a bullet-level corpus exists to avoid, since unrelated work
    -- would drag every posting toward the middle.
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
