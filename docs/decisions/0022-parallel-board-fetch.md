# 0022 — Boards are fetched in parallel; politeness is per host, not global

**Status:** accepted (2026-07-28)

## Context

Ingestion was a single loop over boards, with `get_json` sleeping 0.5 s before every request. That
was a fine proxy for politeness while there were three sources on three hosts and ~123 boards
(~2 minutes). Two things broke it:

- **Board count.** Tier 1 adds 38 more boards.
- **Detail calls.** Three of the six new adapters fetch each posting individually (ADR-0021), so
  requests scale with *postings*, not boards — a measured 1,324 postings over the new sources.

A global sleep makes every one of those requests wait behind every other, including requests to
completely unrelated hosts. It is also the wrong unit: nobody is inconvenienced by us talking to
`{a}.bamboohr.com` and `{b}.pinpointhq.com` at the same instant.

## Decision

**Fetch boards concurrently from one pool across all sources, and enforce the minimum interval
per host.**

- `HostRateLimiter` (`shared/http.py`) keeps consecutive requests to one host `min_interval_s`
  apart and lets different hosts run freely. A slot is *reserved* under the lock and slept on
  outside it, so N threads aiming at one host queue at the interval instead of all reading the
  same stale timestamp and firing together.
- **One pool for every source**, not one per source: the limiter is what serializes, so the pool's
  only job is keeping enough boards in flight that the slowest host — rather than the sum of all
  hosts — sets the wall time. `FETCH_WORKERS` (default 8) bounds it.
- **Workers fetch; the main thread lands.** DuckDB takes a single writer, and keeping
  `storage.land` on one thread also keeps the failure model intact.
- `_fetch_board` **never raises**: a failed board is returned as data. An exception escaping a
  worker would surface at an unrelated `future.result()` and lose which board it belonged to,
  taking per-company isolation with it.
- Each source declares its own `min_interval_s` and timeouts in the registry, so a heavier
  platform can be paced differently without touching any request site.
- Sessions are per thread (`SessionPool`); `requests.Session` is not documented thread-safe.

## Consequences

The interval now buys very different things per platform, and this shapes what a board costs:

- ATS that give every company its **own subdomain** (BambooHR, Recruitee, Pinpoint) parallelize
  almost perfectly — 32 BambooHR boards and their 155 detail calls overlap completely.
- ATS on **one shared host** (Greenhouse, Lever, Ashby, Workable, Rippling, SmartRecruiters) are
  as paced as before, which is the point. A big board on a shared host is therefore intrinsically
  slow: the measured 871-posting SmartRecruiters board took ~10 minutes by itself, and yielded 2
  postings that passed the location gate. That is an argument about which boards to activate, not
  about the rate limit.

Per-source timings in `ops.ingest_runs` are now the **run's** bounds rather than a source's own
slice of wall time — a source no longer owns a contiguous stretch. Row counts, statuses, skipped
refs and the hard-failure rule are unchanged.
