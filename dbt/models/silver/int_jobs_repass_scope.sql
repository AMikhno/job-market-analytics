-- The postings whose stored model result may be REPLACED when one of its inputs
-- moves: the resume, the wording of a prompt, or the model (ADR-0033).
--
-- A posting's first score or assessment is never limited by this -- every
-- posting gets one. This decides only which results are worth paying for a
-- second time, and that is the postings that are still actionable:
--
--   open         a closed posting cannot be applied to, so a better-informed
--                score for it changes nothing anyone will read;
--   not dropped  delivery collapses the drop tier to a line of titles, and what
--                puts a posting there is a different profession or a far-off
--                level, which one more bullet rarely changes.
--
-- A posting no tier has been assigned to is in scope. The assessment may be
-- switched off, behind, or have failed on it, and reading "untiered" as
-- "dropped" would leave a resume edit with no effect at all.
--
-- Everything outside the scope keeps the result it has, under the version it
-- was produced with. That is deliberate: the version columns record which.
--
-- A view, and real SQL on both warehouses, so the rule is unit-tested rather
-- than only run. int_jobs_scored reads it. int_jobs_assessed cannot -- the tier
-- is computed from it -- and applies the same two conditions to its own stored
-- rows, through the macros the tier itself is built from (macros/tier.sql).
{{ config(materialized='view') }}

with open_postings as (
    -- By content_hash, the grain the model results are stored at: identical
    -- text posted twice is open while either posting is.
    select distinct content_hash
    from {{ ref('silver_jobs') }}
    where is_active
)

select o.content_hash
from open_postings o
left join {{ ref('int_jobs_tiered') }} t
    on o.content_hash = t.content_hash
{% if not var('repass_dropped') %}
    where coalesce(t.fit_tier, 'untiered') != 'drop'
{% endif %}
