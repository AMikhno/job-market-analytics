-- How long a posting stays up: a Kaplan-Meier survival curve over days on board.
--
-- A plain average of "days up" would be wrong twice over. Postings still open
-- have not finished their spell, so dropping them biases the answer short and
-- counting them as closed biases it short too; Kaplan-Meier keeps them in the
-- at-risk set until their observation ends (right-censoring). Postings already
-- up when their board was first read have an unknown start, so they are left out
-- entirely (left-censoring) rather than counted with a duration that is too short.
--
-- survival(t) = product over steps <= t of (1 - closed / at_risk), computed as
-- exp(sum(ln(...))) because SQL has no product aggregate. A step where every
-- remaining posting closed has factor 0, which ln cannot take, so it is forced
-- to 0 explicitly.
with spells as (
    select
        company,
        is_closed,
        {{ days_between('first_seen_at', 'last_seen_at') }} as days_up
    from {{ ref('int_market__postings') }}
    where not is_left_censored
),

steps as (
    select distinct days_up as days_on_board from spells
),

risk as (
    select
        s.days_on_board,
        count(*) as at_risk,
        count(distinct sp.company) as at_risk_companies,
        sum(
            case when sp.days_up = s.days_on_board and sp.is_closed then 1 else 0 end
        ) as closed
    from steps s
    inner join spells sp
        on s.days_on_board <= sp.days_up
    group by s.days_on_board
),

factors as (
    select
        days_on_board,
        at_risk,
        at_risk_companies,
        closed,
        1 - cast(closed as {{ dbt.type_float() }}) / at_risk as factor
    from risk
),

curve as (
    select
        days_on_board,
        at_risk,
        at_risk_companies,
        closed,
        case
            when
                min(factor) over (
                    order by days_on_board rows between unbounded preceding and current row
                ) = 0
                then 0
            else exp(sum(ln(case when factor > 0 then factor else 1 end)) over (
                order by days_on_board rows between unbounded preceding and current row
            ))
        end as survival
    from factors
)

-- The at-risk set only shrinks as t grows, so this cuts the tail where the
-- curve would rest on fewer companies than the publishing floor.
select
    days_on_board,
    at_risk,
    at_risk_companies,
    closed,
    -- exp(ln) round-trips with float error (0.4999999...); four places is
    -- finer than any chart can show.
    round(survival, 4) as survival
from curve
where at_risk_companies >= {{ var('market_min_companies') }}
