-- Embedding match: how much of what this posting asks for has a resume bullet
-- behind it?
--
-- Computed one posting sentence at a time (int_posting_units):
--
--   1. each requirement, nice-to-have and responsibility is compared with every
--      work bullet, and keeps its single most similar one;
--   2. `similarity` is the weighted mean of those best matches, a nice-to-have
--      counting for `embedding_nice_to_have_weight` of a requirement.
--
-- Best bullet per sentence, not per posting: a posting matched as one vector
-- is rewarded for resembling one thing the candidate did, however little of
-- the role that is. Averaging the per-sentence bests measures coverage, and a
-- sentence nothing in the resume answers pulls the score down.
--
-- The employer sentence is compared with the industries worked in, not with
-- the bullets, and reported on its own as `industry_similarity`. Blended in, a
-- high score could mean the work fits or only the sector does.
--
-- Runs alongside the LLM's fit_score deliberately and TEMPORARILY. Both answer
-- the same question by different means and nothing yet shows which answers it
-- better, so both run until `make evaluate` compares them against human labels.
--
-- Why embeddings can do this half and not the other: similarity is blind to
-- polarity. "We do not sponsor visas" and "we sponsor visas" are nearly
-- identical vectors, which is why geo_restriction and manages_people stay with
-- the LLM and never move here.
--
-- A plain table, rebuilt every run, and that is the invalidation. Nothing here
-- calls a model: the vectors are stored upstream and this is arithmetic over
-- them, so recomputing all of it is cheaper than deciding which rows a resume
-- edit made stale. An incremental version of this model kept every score from
-- before the edit.
--
-- Real SQL on both warehouses. On DuckDB, and in prod until `enable_embeddings`
-- is set, both vector models are empty stubs and so is this.
{{ config(materialized='table') }}

with posting_vectors as (
    -- Joined to the current units, so a vector stored for a sentence that a
    -- re-extraction has since replaced is not matched.
    select
        u.content_hash,
        u.unit_key,
        u.kind,
        v.vec,
        v.embedding_model,
        v.task_type
    from {{ ref('int_posting_units') }} u
    inner join {{ ref('int_posting_unit_vectors') }} v
        on u.unit_key = v.unit_key
),

pairs as (
    select
        p.content_hash,
        p.unit_key,
        p.kind,
        r.unit_id,
        r.unit_source,
        r.evidences,
        r.resume_version,
        p.embedding_model,
        p.task_type,
        {{ cosine_similarity('p.vec', 'r.vec') }} as similarity
    -- Vectors from different models or task types share no geometry, so a
    -- posting vector left over from before either var changed matches nothing
    -- until it is re-embedded, rather than matching wrongly.
    from posting_vectors p
    inner join {{ ref('int_resume_unit_vectors') }} r
        on
            p.embedding_model = r.embedding_model
            and p.task_type = r.task_type
            and r.kind = case when p.kind = 'company' then 'industry' else 'work' end
),

best_per_unit as (
    select
        content_hash,
        unit_key,
        kind,
        unit_id,
        unit_source,
        evidences,
        resume_version,
        embedding_model,
        task_type,
        similarity,
        case
            when kind = 'nice_to_have' then {{ var('embedding_nice_to_have_weight') }}
            else 1
        end as weight
    from pairs
    where true
    qualify row_number() over (
        partition by unit_key order by similarity desc, unit_id asc
    ) = 1
),

work as (
    select
        content_hash,
        -- nullif: a weight of 0 on a posting with only nice-to-haves would
        -- otherwise divide by zero, which BigQuery raises rather than nulls.
        sum(weight * similarity) / nullif(sum(weight), 0) as similarity,
        count(*) as units_matched,
        max(resume_version) as resume_version,
        max(embedding_model) as embedding_model,
        max(task_type) as task_type
    from best_per_unit
    where kind != 'company'
    group by content_hash
),

-- The bullet behind the posting's single strongest sentence match: the one
-- thing an embedding can say and a 1-5 score cannot.
strongest as (
    select
        content_hash,
        unit_id,
        unit_source,
        evidences
    from best_per_unit
    where kind != 'company'
    qualify row_number() over (
        partition by content_hash order by similarity desc, unit_id asc, unit_key asc
    ) = 1
),

industry as (
    select
        content_hash,
        similarity as industry_similarity
    from best_per_unit
    where kind = 'company'
)

select
    w.content_hash,
    w.similarity,
    i.industry_similarity,
    w.units_matched,
    s.unit_id as best_match_unit_id,
    s.unit_source as best_match_source,
    s.evidences as best_match_evidences,
    w.resume_version,
    w.embedding_model,
    w.task_type,
    {{ dbt.current_timestamp() }} as matched_at
from work w
left join strongest s
    on w.content_hash = s.content_hash
left join industry i
    on w.content_hash = i.content_hash
