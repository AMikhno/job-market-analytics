-- One row per change to a posting, read from its snapshot history: what moved,
-- how long the previous version lasted, and whether the change was one edit or
-- a template change across the board.
--
-- The board-wide flag matters because a posting's text hash cannot tell an edit
-- to that posting from an edit to boilerplate every posting on the board shares.
-- Measured over the first 117 days of landings, half of all text changes arrived
-- in such runs (ADR-0030), so a change count without this flag mostly measures
-- how often employers revise their templates.
with versions as (
    select
        job_key,
        source,
        company,
        title,
        location,
        remote_policy,
        content_hash,
        is_active,
        dbt_valid_from,
        lag(dbt_valid_from) over by_posting as previous_valid_from,
        lag(title) over by_posting as previous_title,
        lag(location) over by_posting as previous_location,
        lag(remote_policy) over by_posting as previous_remote_policy,
        lag(content_hash) over by_posting as previous_content_hash,
        lag(is_active) over by_posting as previous_is_active
    from {{ ref('snap_job_postings') }}
    window by_posting as (partition by job_key order by dbt_valid_from)
),

changes as (
    select
        job_key,
        source,
        company,
        dbt_valid_from as changed_at,
        {{ days_between('previous_valid_from', 'dbt_valid_from') }} as days_in_previous_version,
        content_hash != previous_content_hash as content_changed,
        -- Null and blank mean the same thing on these fields, as in the location
        -- filter: a board publishing "" where it used to omit the field moved nothing.
        coalesce(title, '') != coalesce(previous_title, '') as title_changed,
        coalesce(location, '') != coalesce(previous_location, '') as location_changed,
        coalesce(remote_policy, '')
        != coalesce(previous_remote_policy, '') as remote_policy_changed,
        previous_is_active and not is_active as closed,
        is_active and not previous_is_active as reopened
    from versions
    -- A posting's first version is its arrival, not a change.
    where previous_valid_from is not null
),

board_edits as (
    select
        source,
        company,
        changed_at,
        count(*) as edited_postings
    from changes
    where content_changed
    group by source, company, changed_at
),

-- How many postings were live on the board going into the run that edited them.
-- A snapshot run stamps every version it writes with one timestamp, so the
-- version a run replaced ends exactly at that run's changed_at.
board_open as (
    select
        e.source,
        e.company,
        e.changed_at,
        e.edited_postings,
        count(*) as open_postings
    from board_edits e
    inner join {{ ref('snap_job_postings') }} s
        on
            e.source = s.source
            and e.company = s.company
            and e.changed_at > s.dbt_valid_from
            and e.changed_at <= s.dbt_valid_to
    where s.is_active
    group by e.source, e.company, e.changed_at, e.edited_postings
)

select
    c.job_key,
    c.source,
    c.company,
    c.changed_at,
    c.days_in_previous_version,
    c.content_changed,
    c.title_changed,
    c.location_changed,
    c.remote_policy_changed,
    c.closed,
    c.reopened,
    coalesce(
        c.content_changed
        and b.open_postings >= {{ var('board_wide_edit_min_postings') }}
        and cast(b.edited_postings as {{ dbt.type_float() }}) / b.open_postings
        >= {{ var('board_wide_edit_share') }},
        false
    ) as is_board_wide_edit
from changes c
left join board_open b
    on
        c.source = b.source
        and c.company = b.company
        and c.changed_at = b.changed_at
