-- One row per posting that is still live on its board, with a recency rank and
-- the link out. Carries the V2 fit score where one exists.
--
-- The joins are LEFT and the score columns nullable on purpose: the score
-- orders delivery and never filters it (ADR-0020), so a posting that was not
-- scored -- or could not be -- still ships. On the dev target the scored model
-- is an empty stub, so every row takes that null path.
select
    s.job_key,
    s.content_hash,
    s.source,
    s.company,
    s.title,
    s.location,
    s.remote_policy,
    s.url,
    s.posted_or_updated_at,
    s.ingested_at,
    s.first_seen_at,
    s.last_seen_at,
    s.desired_tech_hits,
    s.title_match,
    -- Negative signal, carried through so delivery can rank on it (ADR-0023).
    -- It never removes a posting: "Kafka" in a nice-to-have line is not the same
    -- as a streaming role, and V1 cannot tell them apart.
    s.deal_breaker_hits,
    s.deal_breaker_terms,
    -- The keyword score (ADR-0024). Kept beside fit_score rather than replaced
    -- by it: until the LLM score is checked against human labels, this is the
    -- number with known behaviour, and the two disagreeing is worth seeing.
    s.match_score,
    -- Out-of-range scores are nulled, not clamped: 7 on a 1-5 rubric is a broken
    -- call, and "fit 5/5" would launder it into a confident number. The upstream
    -- accepted_values test warns on it; this keeps delivery honest meanwhile.
    case
        when sc.fit_score between 1 and 5 then sc.fit_score
    end as fit_score,
    sc.prompt_version,
    sc.scored_at,
    -- The three extracted annotations that change what you do about a posting;
    -- the rest stays in silver. geo_restriction is the one the rule-based gate
    -- provably cannot catch -- silver keeps a bare "Remote" location, so a
    -- US-only role arrives with the restriction stated only in the description.
    st.company_type,
    st.geo_restriction,
    st.manages_people,
    -- The embedding matcher's answer, beside fit_score rather than blended with
    -- it: two unvalidated rankings averaged make a bad result undiagnosable.
    m.similarity,
    m.best_match_source,
    row_number() over (
        order by s.posted_or_updated_at desc nulls last, s.job_key asc
    ) as recency_rank
from {{ ref('silver_jobs') }} s
left join {{ ref('int_jobs_scored') }} sc
    on s.content_hash = sc.content_hash
-- Keyed on content_hash, which is unique in each AI model (tested there, after
-- a fan-out taught us to assert it). Joined independently rather than chained,
-- because extraction can succeed on a posting scoring has not reached yet.
left join {{ ref('int_jobs_structured') }} st
    on s.content_hash = st.content_hash
left join {{ ref('int_jobs_matched') }} m
    on s.content_hash = m.content_hash
-- postings that disappeared from their board are closed — not deliverable
where s.is_active
