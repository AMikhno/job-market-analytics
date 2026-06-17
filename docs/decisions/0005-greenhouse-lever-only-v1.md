# 0005 — Greenhouse + Lever only in V1

**Status:** accepted

Both have public, keyless JSON read APIs, so ingestion needs no credentials. Their field
shapes differ enough to justify per-source bronze staging into a common silver schema.
Other sources are V2+.
