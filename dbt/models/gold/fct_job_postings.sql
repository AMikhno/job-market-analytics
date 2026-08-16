-- One row per posting that is still live on its board, with a recency rank and
-- the link out. Carries the V2 fit score where one exists.
--
-- The join is LEFT and the score columns are nullable on purpose: the score
-- orders delivery, it never filters it (ADR-0020), so a posting that has not
-- been scored -- or could not be, because extraction failed -- still ships.
-- On the dev target the scored model is an empty stub, so every row takes the
-- null path, which is the behaviour worth exercising anyway.
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
    -- by it: until the LLM score is checked against human labels, the rule-based
    -- number is the one with known behaviour, and the two disagreeing is itself
    -- the signal worth seeing.
    s.match_score,
    -- Out-of-range scores are nulled here rather than clamped. A model returning
    -- 7 on a 1-5 rubric is not a strong match, it is a broken call, and showing
    -- "fit 5/5" for it would launder the failure into a confident number. The
    -- accepted_values test upstream fails on it; this keeps delivery honest
    -- meanwhile.
    case
        when sc.fit_score between 1 and 5 then sc.fit_score
    end as fit_score,
    sc.prompt_version,
    sc.scored_at,
    row_number() over (
        order by s.posted_or_updated_at desc nulls last, s.job_key asc
    ) as recency_rank
from {{ ref('silver_jobs') }} s
left join {{ ref('int_jobs_scored') }} sc
    on s.content_hash = sc.content_hash
-- postings that disappeared from their board are closed — not deliverable
where s.is_active
