{# The two conditions that put a posting in the `drop` tier (ADR-0031), as
   macros because two models have to agree on them: int_jobs_tiered assigns the
   tier, and int_jobs_assessed has to know a stored judgement is a drop without
   reading that view, which is built from it (ADR-0033). Each returns a boolean
   that is never null, so it can be negated safely. #}

{# A level far outside the candidate's: an executive title, or several years of
   managing people required. #}
{% macro tier_level_out_of_range(title, people_management_years) -%}
    {%- set executive_titles = ['director', 'vp', 'vice president', 'head', 'chief', 'president'] -%}
    (
        coalesce({{ has_any_word(title, executive_titles) }}, false)
        or coalesce({{ people_management_years }} >= {{ var('tier_max_people_years') }}, false)
    )
{%- endmacro %}

{# A different kind of work, or a level far out of range. #}
{% macro tier_is_drop(work_match, title, people_management_years) -%}
    (
        coalesce({{ work_match }} = 'different', false)
        or {{ tier_level_out_of_range(title, people_management_years) }}
    )
{%- endmacro %}
