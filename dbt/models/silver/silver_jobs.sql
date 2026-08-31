-- Dedup to the current row per posting, derive its lifecycle (is it still on the
-- board?), then annotate it with keyword signals (no LLM in V1). Deal-breaker
-- tech, desired tech/titles and the Canada location marker are all seed-driven,
-- so the rules are data, not hardcoded SQL.
--
-- Location is the only hard filter left. Deal-breaker tech used to drop a posting
-- outright; it is now a negative *signal* (ADR-0023) because a single mention
-- anywhere in the text -- including a "nice to have" line -- was deleting roles
-- that matched on everything else.
with lifecycle as (
    select
        *,
        -- the earliest ingest that saw this posting: a job_key whose first_seen_at
        -- falls in the current run is net-new (the "new since last run" signal).
        min(ingested_at) over (partition by job_key) as first_seen_at,
        -- when this posting was last seen on its board, across all ingests
        max(ingested_at) over (partition by job_key) as last_seen_at,
        -- the board's most recent ingest: a posting absent from it was taken down
        max(ingested_at) over (partition by source, company) as board_last_ingested_at,
        -- the latest ingest across the whole pipeline: the staleness yardstick
        -- for boards that stopped being ingested at all (see is_active below)
        max(ingested_at) over () as pipeline_last_ingested_at
    from {{ ref('int_jobs__unioned') }}
),

deduped as (
    select *
    from lifecycle
    -- `where true` keeps BigQuery's parser happy: QUALIFY historically requires a
    -- WHERE / GROUP BY / HAVING clause alongside it.
    where true
    -- The raw landing is append-only, so "most recently ingested" is the current
    -- version of a posting. posted_or_updated_at can't order this: Lever only
    -- exposes createdAt, which never changes, so every re-ingest would tie.
    qualify row_number() over (
        partition by job_key
        order by ingested_at desc, posted_or_updated_at desc nulls last
    ) = 1
),

-- Deal-breaker tech named anywhere in the posting text (word match). Counted and
-- named, not filtered: `deal_breaker_hits` = how many distinct terms appear, and
-- `deal_breaker_terms` says which, so a posting can be judged rather than vanish.
deal_breaker_matches as (
    select
        d.job_key,
        count(t.tech) as deal_breaker_hits,
        string_agg(t.tech, ', ' order by t.tech) as deal_breaker_terms
    from deduped d
    inner join {{ ref('deal_breaker_tech') }} t
        on {{ regexp_word_ci('d.clean_text', 't.tech') }}
    group by d.job_key
),

-- Location rule (V1, deliberately coarse): keep a posting whose location is
-- unknown, is bare "Remote", or word-matches an allowed marker from the seed.
-- Word-matched, not substring, so a province abbreviation matches "City, XX"
-- and not a longer word containing those letters. No country blocklist, so a
-- posting whose location names an out-of-scope country is dropped only because
-- it matches nothing; V2's LLM does true location eligibility.
--
-- "Unknown" is null OR blank. Adapters pass the ATS field through untouched, so
-- a board that publishes an empty location string and one that omits the field
-- mean the same thing and must be treated the same; testing only for null once
-- made the first kind lose every posting while the second kept them.
location_ok as (
    select d.job_key
    from deduped d
    left join {{ ref('allowed_locations') }} a
        on {{ regexp_word_ci('d.location', 'a.pattern') }}
    group by d.job_key
    having
        coalesce(trim(max(d.location)), '') = ''
        or lower(trim(max(d.location))) = 'remote'
        or count(a.pattern) > 0
),

-- Soft signals (ADR-0015): annotate, never drop. desired_tech_hits counts how
-- many desired techs the posting text names; title_match flags a targeted title.
-- V1 keeps everything and lets delivery sort/filter; V2's LLM does the judgment.
desired_tech_counts as (
    select
        d.job_key,
        count(t.tech) as desired_tech_hits
    from deduped d
    left join {{ ref('desired_tech') }} t
        on {{ regexp_word_ci('d.clean_text', 't.tech') }}
    group by d.job_key
),

title_matches as (
    select
        d.job_key,
        count(p.pattern) > 0 as title_match
    from deduped d
    left join {{ ref('desired_titles') }} p
        on {{ regexp_word_ci('d.title', 'p.pattern') }}
    group by d.job_key
)

select
    d.job_key,
    d.content_hash,
    d.source,
    d.company,
    d.external_id,
    d.title,
    d.location,
    d.remote_policy,
    d.department,
    d.employment_type,
    d.url,
    d.description_html,
    d.clean_text,
    d.posted_or_updated_at,
    d.ingested_at,
    d.first_seen_at,
    d.last_seen_at,
    coalesce(dtc.desired_tech_hits, 0) as desired_tech_hits,
    coalesce(tm.title_match, false) as title_match,
    -- Negative signal, never a filter (ADR-0023). 0 = clean.
    coalesce(dbm.deal_breaker_hits, 0) as deal_breaker_hits,
    dbm.deal_breaker_terms,
    -- The one number delivery orders on (ADR-0024). Every input is visible in
    -- the digest line beside it, so the ranking explains itself; the weights are
    -- vars, so tuning them needs no code change. May go negative.
    (
        coalesce(dtc.desired_tech_hits, 0)
        + {{ var('match_title_bonus', 2) }}
        * case when coalesce(tm.title_match, false) then 1 else 0 end
        - {{ var('match_deal_breaker_penalty', 1) }} * coalesce(dbm.deal_breaker_hits, 0)
    ) as match_score,
    -- Active = still on the board as of that board's latest ingest, AND that
    -- board is itself still being ingested. Without the second clause, a board
    -- removed from the company list (or 404-ing forever - per-company failures
    -- only warn) freezes its board_last_ingested_at and its postings would stay
    -- "active" in gold indefinitely. The grace window tolerates ~2 consecutive
    -- failed runs at the twice-daily cadence (mirroring the 30h freshness gate)
    -- and self-heals: a recovering board's postings are re-seen and reactivate.
    (
        d.last_seen_at >= d.board_last_ingested_at
        and d.board_last_ingested_at >= {{ timestamp_hours_before(
            'd.pipeline_last_ingested_at', var('board_staleness_hours', 36)
        ) }}
    ) as is_active
from deduped d
left join desired_tech_counts dtc on d.job_key = dtc.job_key
left join title_matches tm on d.job_key = tm.job_key
left join deal_breaker_matches dbm on d.job_key = dbm.job_key
where d.job_key in (select location_ok.job_key from location_ok)
