# 0005 — Greenhouse + Lever only in V1

**Status:** superseded in part by ADR-0013

Both have public, keyless JSON read APIs, so ingestion needs no credentials. Their field
shapes differ enough to justify per-source bronze staging into a common silver schema.

The original "other sources are V2+" limit no longer holds: ADR-0013 broadens V1 to **any
ATS with a public, keyless feed** (Ashby shipped in V1.5; BambooHR/Workday are tentative V2;
iCIMS is deferred). What survives from this ADR is the *reasoning* — public + keyless is the
bar, and each distinct field shape earns its own per-source staging model.
