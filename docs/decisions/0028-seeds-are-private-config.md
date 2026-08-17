# 0028 — The filter-rule seeds are private config

**Status:** accepted (refines ADR-0015, ADR-0023, ADR-0024)

The four dbt seeds — `allowed_locations`, `deal_breaker_tech`, `desired_tech`,
`desired_titles` — were tracked files. They are personal context, and CLAUDE.md already
said so: work eligibility and preferences are named there as *input to decisions, never
content*. `allowed_locations` is where the candidate may work; the other three are what
they want and what they refuse, with `deal_breaker_tech.reason` stating career positioning
outright. Tracking them contradicted the repo's own boundary.

**Decision: the seeds resolve like the company list.** `dbt/seeds/*.csv` is gitignored,
`config/seeds/*.example.csv` is committed, and `make seeds` puts one in place before any dbt
target that parses the project. Precedence is environment variable → private file → example.

**Variables, not secrets.** The same test the company list passes (ADR-0011): secrets are for
credentials and for the resume corpus, whose employer history and work authorization are a
different order of sensitivity (ADR-0027). These are preferences. Masking them would make CI
logs harder to read for no gain, and four unencrypted Actions variables is the honest
description of what they are.

**Examples live outside `dbt/seeds/`**, in `config/seeds/`. dbt loads *every* CSV under
`seed-paths`, so `desired_tech.example.csv` beside the real file would materialize a second,
junk seed table and put it in the DAG.

**`seeds.yml` stays tracked.** The schema tests are the mechanism, and the mechanism is the
part worth reading: what is private is which locations and which technologies, not that a
`not_null`/`unique` contract exists over them.

**A missing seed falls back with a warning rather than failing.** A fork PR and a fresh clone
have neither variable nor private file, and both must still build — CI's no-secrets workflow
is exactly that case. The examples are generic, so the resulting gold is someone else's
shortlist rather than a wrong one. A *malformed* seed is a hard error: `materialize_seeds`
asserts each header, because dbt would otherwise accept a renamed column and fail in whichever
model refs it, one layer from the cause.

**This does not retract what is already published.** The seed contents are in this public
repo's history, and the decision here stops future drift rather than removing them. Rewriting
history on a pushed public branch was considered and rejected as disproportionate to
preferences that were never credentials.

**Cost.** Four more Actions variables to keep current, and a build step that must run before
dbt parses. The failure mode is loud — a missing seed breaks `ref()` resolution at compile
time, not silently at run time.
