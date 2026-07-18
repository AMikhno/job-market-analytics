# 0016 — silver is the retention layer; gold is live-only

**Status:** accepted

Where do postings that vanish from a board go? The pipeline never hard-deletes one: the raw
landing is append-only, and `silver_jobs` keeps **one row per `job_key`** carrying
`first_seen_at`, `last_seen_at`, and `is_active`. A taken-down posting simply flips to
`is_active = false` (its `last_seen_at` falls behind its board's latest ingest) and stays in
silver.

**Decision.** `silver_jobs` is the system of record for *all* postings, live and closed. Gold
(`fct_job_postings`) deliberately filters to `is_active` — it is the live delivery view, and a
closed posting must not surface as an apply link. Anyone who needs closed postings (analytics,
"was this filled?", not re-surfacing them) queries silver directly, where `is_active` /
`last_seen_at` make the state explicit.

No separate archive mart is built in V1: it would have no consumer yet (the deliverable is live
links) and this repo avoids dead code. The hard retention floor is raw's ingestion-time
partition expiry (`bq_raw_partition_expiry_days`, 400 days — `shared/storage.py`), after which a
long-closed posting's raw rows age out and it leaves silver on the next rebuild. If a browsable
archive of closed postings is wanted later, add a thin gold view over
`silver_jobs where not is_active` then.
