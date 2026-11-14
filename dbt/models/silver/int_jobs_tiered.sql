-- The tier each assessed posting falls in, computed from the facts the
-- assessment returned (ADR-0031). A view on purpose: the thresholds are vars,
-- and a rule change must re-tier every posting without re-billing a model call.
--
--   fit        nothing an employer would refuse over, and at most a waivable gap
--   near       several waivable gaps, or a role below the candidate's level
--   reach      at least one gap an employer would not waive, or a kind of work
--              listed only as an interest -- worth trying, not likely
--   unrelated  a different field, or a level too far away in either direction
--
-- Order matters and is the rule: a posting is unrelated before it is anything
-- else, and a hard gap outranks every soft consideration. `unrelated` is the
-- only tier delivery collapses to a count; nothing is deleted anywhere.
--
-- A null level_distance (the posting did not say) fails both level tests, so an
-- unstated level never moves a posting.
{{ config(materialized='view') }}

with tiered as (
    select
        content_hash,
        field_relation,
        level_distance,
        must_have_count,
        soft_gap_count,
        hard_gap_count,
        soft_gaps,
        hard_gaps,
        why,
        gap,
        profile_version,
        assessed_at,
        case
            when field_relation = 'unrelated' then 'unrelated'
            when abs(level_distance) > {{ var('tier_max_level_distance') }} then 'unrelated'
            when hard_gap_count > 0 then 'reach'
            when field_relation = 'interest' then 'reach'
            when soft_gap_count > {{ var('tier_fit_max_soft_gaps') }} then 'near'
            when level_distance < 0 then 'near'
            else 'fit'
        end as fit_tier
    from {{ ref('int_jobs_assessed') }}
    -- A failed generation has no facts; tiering it would read "no gaps" as fit.
    where assess_ok
)

select
    content_hash,
    fit_tier,
    -- The tier as a number, larger is better: what delivery and the evaluation
    -- sort on.
    cast(
        case fit_tier
            when 'fit' then 3
            when 'near' then 2
            when 'reach' then 1
            else 0
        end as {{ dbt.type_bigint() }}
    ) as tier_rank,
    field_relation,
    level_distance,
    must_have_count,
    soft_gap_count,
    hard_gap_count,
    soft_gaps,
    hard_gaps,
    why,
    gap,
    profile_version,
    assessed_at
from tiered
