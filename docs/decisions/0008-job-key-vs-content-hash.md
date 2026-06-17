# 0008 — Separate identity key from change-detection hash

**Status:** accepted

`job_key` = surrogate of (source, company, external_id): stable identity, used for dedup
(keep latest per key). `content_hash` = surrogate of (title, clean_text): changes when the
posting text changes, used as V2's incremental reprocessing key so edited postings get
re-extracted while unchanged ones are skipped.
