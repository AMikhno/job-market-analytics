# 0013 — V1 ingestion broadens beyond Greenhouse + Lever

**Status:** accepted (supersedes the scope limit in ADR-0005)

ADR-0005 restricted V1 to Greenhouse and Lever because both expose public, keyless JSON
APIs and V1 needs no credentials. That reasoning constrains the *mechanism*, not the
*count*: the target job market spans many ATS, so V1 now ingests every ATS that offers a
public, keyless posting feed — one adapter per access method (per CLAUDE.md), unified into
the common `RawPosting` schema.

**Added now — Ashby.** `GET api.ashbyhq.com/posting-api/job-board/{board_ref}?includeCompensation=true`
is public and keyless and returns a single `{"jobs": [...]}` response with no pagination, so
its adapter mirrors Greenhouse/Lever exactly (one GET, map each item). Unlike Greenhouse,
Ashby's `descriptionHtml` is already real HTML, so it is passed through, not unescaped.

**Planned, in feasibility order (from live API research):**

- **BambooHR** — public `{company}.bamboohr.com/careers/list` plus a per-id
  `/careers/{id}/detail`. Two-step and undocumented; the internal shape shifts between
  releases, so it needs a fragility guard and a committed fixture.
- **Workday** — `POST {tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs`,
  offset pagination (`limit` ≤ 20, `total` returned) plus a per-job detail call; `board_ref`
  carries `tenant/wdN/site`. It cannot use today's single-GET `get_json`, so it depends on a
  generalized adapter/HTTP contract (POST + pagination + detail) built alongside it.

**Deferred — iCIMS.** There is no clean public, keyless API: the Job Portal API needs
customer context, the standard feed requires OAuth2, and public career sites are scrape-only
and shape-sensitive. Ingesting it would either break the "no credentials in V1" boundary
(ADR-0007) or take on brittle scraping, so iCIMS stays inventory-only (`active=false`) until
we deliberately accept one of those costs. Custom career pages are later still.

The secrets boundary (ADR-0007) is unchanged: only sources with public, keyless feeds are
switched on; anything requiring auth stays off.
