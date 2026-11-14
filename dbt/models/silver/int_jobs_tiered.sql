-- The tier each assessed posting falls in (ADR-0031). A view on purpose: the
-- rule and its thresholds live here, so changing either re-tiers every posting
-- on the next run without a model call.
--
--   best_fit  the candidate's own work, with their own tools, at mid or senior
--   fit       mostly that work, or an equivalent tool, or a level that is not
--             the best one: junior, staff, or leading a team
--   stretch   a neighbouring discipline, or a role built around an ecosystem
--             the candidate does not use
--   drop      a different kind of work, or a level far out of range
--
-- First match wins, worst first. Only `drop` is collapsed by delivery, and it is
-- deliberately narrow: an unusual level alone never drops a posting, it only
-- keeps it out of best_fit.
--
-- The LEVEL is read here, not asked of the model, from columns that already
-- exist: the title's seniority words, the years the posting requires, and
-- whether it manages people. Each of those is a fact a rule reads more
-- consistently than a model summarises.
{{ config(materialized='view') }}

{%- set executive_titles = ['director', 'vp', 'vice president', 'head', 'chief', 'president'] %}
{%- set staff_titles = ['staff', 'principal', 'distinguished'] %}
{%- set junior_titles = ['junior', 'jr', 'intern', 'internship', 'graduate', 'entry level'] %}

with titles as (
    select
        content_hash,
        any_value(title) as title
    from {{ ref('silver_jobs') }}
    group by content_hash
),

levelled as (
    select
        a.content_hash,
        a.work_match,
        a.stack_gap,
        a.people_management_years,
        a.why,
        a.gap,
        a.profile_version,
        a.assessed_at,
        case
            when coalesce({{ has_any_word('ti.title', executive_titles) }}, false)
                then 'out_of_range'
            when a.people_management_years >= {{ var('tier_max_people_years') }} then 'out_of_range'
            when s.manages_people = 'yes' then 'lead'
            when coalesce({{ has_any_word('ti.title', staff_titles) }}, false) then 'staff'
            when s.years_experience_min >= {{ var('tier_staff_min_years') }} then 'staff'
            when coalesce({{ has_any_word('ti.title', junior_titles) }}, false) then 'junior'
            when s.years_experience_min <= {{ var('tier_junior_max_years') }} then 'junior'
            else 'mid_senior'
        end as level_band
    from {{ ref('int_jobs_assessed') }} a
    left join {{ ref('int_jobs_structured') }} s
        on a.content_hash = s.content_hash
    left join titles ti
        on a.content_hash = ti.content_hash
    -- A failed generation has no judgement, and an answer outside the two
    -- vocabularies is not one this rule can read. Both stay untiered -- shown as
    -- not assessed -- rather than falling through to a tier by default.
    where
        a.assess_ok
        and a.work_match in ('same', 'close', 'adjacent', 'different')
        and a.stack_gap in ('none', 'substitutable', 'central')
),

tiered as (
    select
        content_hash,
        work_match,
        stack_gap,
        level_band,
        why,
        gap,
        profile_version,
        assessed_at,
        case
            when work_match = 'different' or level_band = 'out_of_range' then 'drop'
            when work_match = 'adjacent' or stack_gap = 'central' then 'stretch'
            when work_match = 'close' or stack_gap = 'substitutable' then 'fit'
            when level_band != 'mid_senior' then 'fit'
            else 'best_fit'
        end as fit_tier
    from levelled
)

select
    content_hash,
    fit_tier,
    -- The tier as a number, larger is better: what delivery and the evaluation
    -- sort on.
    cast(
        case fit_tier
            when 'best_fit' then 3
            when 'fit' then 2
            when 'stretch' then 1
            else 0
        end as {{ dbt.type_bigint() }}
    ) as tier_rank,
    work_match,
    stack_gap,
    level_band,
    why,
    gap,
    profile_version,
    assessed_at
from tiered
