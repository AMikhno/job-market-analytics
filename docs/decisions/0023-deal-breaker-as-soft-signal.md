# 0023 — Deal-breaker tech demotes a posting; it no longer deletes it

**Status:** accepted (2026-07-28). Extends ADR-0015 (soft desired signals) to the negative case;
supersedes the hard filter described in ADR-0001 and [filtering](../../ARCHITECTURE.md#filtering-one-rule-removes-the-rest-rank).

## Context

Since V1, a posting whose text word-matched any `deal_breaker_tech` seed term (Kafka, Spark,
Flink, Scala, Hadoop) was removed from `silver_jobs` outright. It never reached gold, so it never
reached the digest — regardless of title, location or how many desired technologies it named.

Three properties of that rule turned out to be wrong in combination:

1. **It matched the whole description.** `clean_text` is the entire posting with HTML stripped —
   requirements, nice-to-haves, the company's stack blurb, boilerplate. There is no section
   awareness.
2. **One mention was enough.** No threshold, no weighting.
3. **It ran before every other signal.** A perfect title with six desired technologies died on a
   single "Kafka a plus".

Measured on a full 167-board run (2026-07-28): **123 postings passed the location gate and were
then deleted by this rule.**

| | |
|---|---:|
| deleted | 123 |
| …with ≥2 desired technologies | 55 |
| …with ≥3 | 30 |
| …matching the desired-titles seed | 8 |
| …killed by exactly **one** distinct term | **88 (72%)** |
| …naming a deal-breaker **in the title** | **0** |

Casualties included *Senior Software Engineer, AI Productivity* (4 desired techs, one Kafka
mention), *Senior Data Platform Engineer*, and several *Data Engineer* roles carrying 4–6 desired
technologies — all of them roles the rule was never meant to exclude.

The rule was trying to express "I don't want a streaming/big-data engineering job". What it
actually expressed was "I don't want any posting that mentions these words anywhere", and since
**no** deleted posting named a deal-breaker in its title, the signal it keyed on was never the
one that identified the job.

## Decision

**Deal-breaker tech becomes a negative signal, not a filter.** `silver_jobs` gains:

- `deal_breaker_hits` — how many distinct terms matched (0 = clean);
- `deal_breaker_terms` — which ones, sorted and comma-separated, null when none.

Both flow to gold. The digest **orders by `deal_breaker_hits` ascending first**, so flagged
postings appear below clean ones, and prints the matched terms in the posting's signal line
("mentions Flink, Kafka, Spark"). Nothing is removed.

**Location is now the only hard filter in silver.**

This makes the negative case consistent with ADR-0015 (desired tech/title annotate, never drop)
and with ADR-0020 (the V2 fit score orders delivery, never filters it). The pipeline now has one
rule about rules: V1 keyword signals rank; only geography excludes.

## Consequences

- Gold grew from 1,179 to **1,302** postings on the measured run. 46 of the 123 recovered have no
  desired technology at all and will sort to the bottom; 30 carry three or more.
- **"Spark" alone accounts for 57 of the 123**, and single-term matches for 88 — the shape you
  would expect from nice-to-have lists rather than from streaming roles.
- The digest gets longer. That is the accepted cost: a posting you can dismiss from its one-line
  summary is cheaper than one you never learn exists.
- Naming the terms matters more than counting them. "Kafka" reads very differently from
  "Flink, Hadoop, Kafka, Scala, Spark", and the reader can tell them apart at a glance where the
  pipeline cannot.
- **This is a stopgap for a judgment V1 cannot make.** Distinguishing "Kafka required" from
  "Kafka a plus" — and an analytics role that happens to mention Spark from a streaming
  engineering role — is reading comprehension, i.e. V2's scorer (ADR-0020). When that lands,
  `deal_breaker_terms` becomes an input to the score rather than a sort key.

## Alternatives rejected

- **Threshold at ≥2 distinct terms** — would have recovered 88 of 123 and kept the genuinely
  streaming-heavy roles out. Rejected as arbitrary: it encodes a guess about how many times a
  technology must appear to matter, which is exactly the judgment being deferred to V2.
- **Scope the match to the title** — would have recovered all 123, since none name a
  deal-breaker in the title. Rejected because it makes the rule almost inert: it would only ever
  catch a title like "Senior Spark Engineer", which the desired-title signal already fails to
  match anyway.
- **Weighting the tech seeds** (strong BI tools scoring above generic SQL/Python/AWS) was
  considered alongside this and rejected outright: an analytics role with no BI tooling and a
  Tableau-heavy pure-frontend role are the same keyword footprint with opposite verdicts. No
  weighting of term presence can separate them.
