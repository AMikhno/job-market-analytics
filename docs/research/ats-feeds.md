# ATS feed survey — which platforms qualify for a V1 adapter

**Date:** 2026-07-28 · **Method:** live probes, not recall · **Applies:** ADR-0013 (a V1 source
needs a public, keyless feed; everything else stays inventory-only)

The inventory had grown to ~25 ATS without anyone knowing which were reachable. This survey
answers that per platform, with evidence. Every "cheap" verdict below is a live HTTP 200 with
parsed JSON job records, fetched using a **real `board_ref` already in the company list** — not
a documented endpoint, not a recalled one. Several platforms whose APIs are widely described as
public turned out to be authenticated or removed, which is exactly why this is measured.

Reproduce with `tools/company_discovery/ats_feed_probe.py` (reads refs from the private list).

Counts are companies in the 141-row master as of this date; the 873-company re-audit will shift
them, probably upward for Tier 1, since small companies favour BambooHR and Workable.

---

## Tier 1 — cheap: same shape as Greenhouse/Lever/Ashby

Single keyless `GET`, JSON body, bare-token ref. Each is a ~30-line adapter plus a sanitized
fixture and tests — the `ingest/adapters/ashby.py` pattern verbatim, no new access method.

| ATS | Endpoint | Probe result | Companies |
|---|---|---|---:|
| **BambooHR** | `https://{ref}.bamboohr.com/careers/list` | 200, `{meta, result[]}` — solace 26, REDACTED 4, REDACTED 2, REDACTED 1 | **31** |
| **Workable** | `https://apply.workable.com/api/v1/widget/accounts/{ref}?details=true` | 200 JSON (REDACTED-inc) | **5** |
| **Recruitee** | `https://{ref}.recruitee.com/api/offers/` | 200, 167 offers (REDACTED) | **5** |
| **BreezyHR** | `https://{ref}.breezy.hr/json` | 200 JSON (REDACTED) | 2 |
| **Pinpoint** | `https://{ref}.pinpointhq.com/postings.json` | 200, 37 postings (bmt) | 2 |
| **SmartRecruiters** | `https://api.smartrecruiters.com/v1/companies/{ref}/postings?limit=100` | 200, 100 (capped — paginated) | 2 |
| **Rippling** | `https://api.rippling.com/platform/api/ats/v1/board/{ref}/jobs` | 200, 28 jobs (REDACTED) | 1 |

**~48 companies** unlockable. BambooHR alone is 31 — the highest payoff-to-effort work available
in the project. SmartRecruiters is the only one needing pagination (`limit`/`offset`).

Note Workable's **v1 widget** endpoint is the working one; the `api/v3/accounts/{ref}/jobs` path
that appears in newer docs returned 404 for every ref tried.

**The real work is mapping, not fetching.** Each platform names its fields differently, so the
per-adapter cost is the `RawPosting` mapping and its fixture, not the HTTP call.

## Tier 2 — reachable, but a different access method

| ATS | Finding | Companies |
|---|---|---:|
| **Workday** | `POST /wday/cxs/{tenant}/{site}/jobs` returned **422, not 404** — the endpoint is live and keyless, but needs a POST body, offset pagination, **and** a multi-segment ref (tenant / wd-number / site). The list stores tenant only (`REDACTED`, `REDACTED`), so every row needs its site captured before any code runs. This is the case ADR-0012's `board_ref` design was written for. | **28** |
| **Eightfold** | 403 on `api/apply/v2/jobs` for both refs. Possibly fixable with correct domain param/headers; unproven. | 2 |
| **Jobvite, Oracle HCM, Paylocity** | **Untested — no usable `board_ref` stored** (blank refs). Cannot be judged until discovery captures one. | 9 |

## Tier 3 — not viable for V1 (stays inventory-only)

| ATS | Finding | Companies |
|---|---|---:|
| **Dayforce** | 404 on the candidate-portal API; the v2 path redirect-loops. Per-tenant portal, no public feed. | 10 |
| **Indeed** | Aggregator, ToS-restricted. Already parked (ADR-0013). | 9 |
| **ADP** | 500 from the public staffing endpoint. | 6 |
| **Phenom** | 404; endpoint is per-tenant and inconsistent. | 4 |
| **iCIMS** | HTML only, no keyless API — confirms ADR-0013's existing verdict. | 3 |
| **JazzHR** | No JSON or RSS feed found (`/apply/jobs.json` 404, `/rss` returns HTML). API needs a key. | 3 |
| **SuccessFactors** | **401** — OData requires auth. Definitively not keyless. | 2 |
| **Teamtailor** | `jobs.json` 404; the public API requires a per-company `X-Api-Key`. | 1 |

---

## Suggested order

1. **BambooHR** — 31 companies, one small adapter.
2. **Workable + Recruitee** — 10 more, same pattern.
3. **BreezyHR, Pinpoint, SmartRecruiters, Rippling** — 7 more; do them together, they are nearly
   identical.
4. **Workday** — decide separately. It needs a ref-schema pass across 28 rows *before* any code,
   and it is a new adapter class rather than a copy of an existing one.

## Caveats

- Each verdict rests on one or two refs. Check a second ref per platform before committing to an
  adapter — a single company's board can be misconfigured in ways that look like a platform-wide
  answer.
- A 200 with zero records is not proof of a working feed (an empty stub board looks identical);
  Tier 1 verdicts all had **non-zero** records except where noted.
- Endpoints move. Re-run the probe before starting, rather than trusting this file's age.
