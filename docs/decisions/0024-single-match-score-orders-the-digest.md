# 0024 — One visible `match_score` orders the digest

**Status:** accepted (2026-07-28). Refines the delivery ordering in ADR-0019 and the signal
semantics in ADR-0015 / ADR-0023. Does **not** touch ADR-0020's V2 `fit_score`.

## Context

The digest ordered postings by three keys in priority order:

```sql
order by deal_breaker_hits asc, title_match desc, desired_tech_hits desc, posted_or_updated_at desc
```

but each line printed only `tech hits: N`. Because `title_match` outranked `desired_tech_hits`,
the printed number **reset partway down the email**. On real data (167 boards, 1,302 gold
postings) the top of the list runs `tech=9, 7, 7, 7, 6 …` down to `tech=0, tech=0` — and then
jumps back to `tech=8`, where the title-matched block ends and the rest begins. The list is
sorted; it just isn't sorted by anything the reader can see, so it reads as unsorted.

Sorting on the flag first had a second problem: it put *every* posting mentioning a deal-breaker
below *every* clean one. A posting with six desired technologies, a title match and one
incidental "Kafka" ranked below an unrelated posting with no signals at all — which contradicts
the reasoning in ADR-0023 that a single mention should cost a posting something, not everything.

## Decision

**Compute one integer `match_score` in silver, carry it to gold, and order delivery by it alone.**

```
match_score = desired_tech_hits
            + match_title_bonus        * (title_match ? 1 : 0)
            − match_deal_breaker_penalty * deal_breaker_hits
```

- Weights are **dbt vars** (`match_title_bonus: 2`, `match_deal_breaker_penalty: 1`), so tuning
  needs no code change. Integers only — the models declare `match_score` under a dbt contract.
- **Technologies carry the weight, deliberately.** The target titles are too varied to enumerate
  (Snowflake developer, BI migration, AI productivity analyst, Product analyst), so the title
  seed is a nudge and not a gate.
- The digest prints the score first and its inputs beside it:
  `[match 7 — tech hits: 6, title match, mentions Kafka]`. **The number shown is the number
  sorted on**, so any ordering can be checked from the line itself.
- `recency_rank` stays in gold. "Best fit" and "newest" are different questions and both are
  worth querying.
- The score may be **negative** (deal-breakers exceeding signal). Those sort last, which is what
  a strongly streaming-flavoured posting deserves without being deleted.

## Consequences

- A strong match with one incidental deal-breaker now outranks a weak clean posting, and loses
  only to its own twin without the mention. This is a deliberate reversal of the
  flag-first ordering introduced hours earlier in ADR-0023, on the same reasoning that motivated
  0023 in the first place.
- The weights are a judgment encoded as data. They are almost certainly wrong in detail — the
  point is that they are visible, tunable, and printed next to every posting, so being wrong is
  observable rather than mysterious.
- **This is not a fit score and must not grow into one.** ADR-0020 reserves `fit_score` (1–5,
  LLM-assigned) for V2, and `docs/v2-plan.md` has the digest ordering by it. When V2 lands,
  `match_score` becomes a secondary key (or a feature the scorer reads), not a competitor.
  Deliberately different names so the two never get confused in a query.

## Alternatives rejected

- **Keep multi-key ordering, print all the keys.** Honest, but it asks the reader to do a
  lexicographic sort in their head to check the order.
- **Weight individual technologies** (BI tools above generic SQL/Python/AWS) — rejected in
  ADR-0023: an analytics role with no BI tooling and a Tableau-heavy pure-frontend role have the
  same keyword footprint and opposite verdicts.
- **Normalize to 0–100.** Invites reading it as a probability of fit. It is a count of keyword
  evidence, and looking like one is a feature.
