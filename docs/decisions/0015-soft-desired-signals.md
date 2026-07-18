# 0015 — Desired tech + title matches are soft signals, not filters

**Status:** accepted

V1 already has two *hard* silver filters that drop rows: deal-breaker tech and the Canada
location gate (both seed-driven and word-matched). The obvious next step — "only keep
Analytics/BI titles" and "only keep postings that name my stack" — is deliberately **not**
built as a drop.

**Decision: annotate, never drop.** Silver derives two soft columns from seeds:

- `desired_tech_hits` — how many `desired_tech` terms the posting text word-matches (a count);
- `title_match` — whether the title word-matches a `desired_titles` pattern (a boolean).

Both flow through to gold; nothing is filtered on them. Delivery (and a human) sort or filter
downstream.

**Why soft.** A keyword title/tech filter has no notion of required-vs-nice-to-have or
seniority — the exact judgment V1 defers to V2's LLM. A hard title include-list would silently
drop a relevant role phrased unusually ("Insights Engineer", "Data & Reporting Lead"), and a
hard tech gate would drop a good posting that simply doesn't spell out the stack. In V1 the
cost of a false *drop* (a missed job) outweighs the cost of a false *keep* (one extra row to
skim), so recall wins: keep everything, rank later.

The seeds (`desired_tech.csv`, `desired_titles.csv`) are data, like the existing filter seeds,
so the lists are tunable without code. Whether any of these become hard filters is a V2
decision, once the LLM can judge fit.
