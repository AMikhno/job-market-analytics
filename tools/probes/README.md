# One-off probes

Scripts that produced the measurements in `docs/research/`. They are **not** part of the pipeline:
nothing imports them, CI does not run them, and they carry no tests. They are here so the numbers
in the research docs are reproducible rather than asserted, and because they were previously kept
in a session scratchpad under `/private/tmp`, which macOS purges after a few days.

Run them with `uv run python tools/probes/<dir>/<script>.py`. Several hit live third-party
endpoints — they are polite (single-threaded, delays) but they are still real traffic, so do not
loop them.

| Directory | Produced | Doc |
|---|---|---|
| `workday/` | Workday endpoint probes, ref recovery by website scrape, detail-payload check, Tier-2 sweep across the unbuilt sources | `docs/research/workday-ref-discovery.md` |
| `careers-tail/` | Shape of the 334 custom careers pages; JS-rendered vs static; BambooHR board liveness | `docs/research/careers-page-tail.md` |

`careers-tail/probe_bamboohr.py` is the generic-shaped one: it probes every active board of a
source's list endpoint and reports live / empty / failed. Worth reaching for whenever a source
looks stale and you want an answer that does not need BigQuery access.
