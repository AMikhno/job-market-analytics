# ATS feed survey — which platforms qualify for a V1 adapter

**Date:** 2026-07-28 · **Method:** live probes, not recall · **Applies:** ADR-0013 (a V1 source
needs a public, keyless feed; everything else stays inventory-only)

The inventory had grown to ~25 ATS without anyone knowing which were reachable. This survey
answers that per platform, with evidence. Every "cheap" verdict below is a live HTTP 200 with
parsed JSON job records, fetched using a **real `board_ref` already in the company list** — not
a documented endpoint, not a recalled one. Several platforms whose APIs are widely described as
public turned out to be authenticated or removed, which is exactly why this is measured.

Reproduce with `tools/company_discovery/ats_feed_probe.py` (reads refs from the private list).

**Counts updated 2026-07-28 after the 873-company re-audit.** They are companies sitting
`active=false` in the rebuilt 285-row master — i.e. the companies each adapter would unlock.
The master now runs **123 active boards** (Greenhouse 57, Ashby 56, Lever 10), all verified
resolving. The prediction that Tier 1 would grow held: BambooHR went 31 → 33 and Recruitee
5 → 6, though the biggest mover was Teamtailor (1 → 6), which is Tier 3.

---

> **Re-probed 2026-07-28 before building the adapters, and the "same shape" verdict below did not
> survive contact.** Only Recruitee, Workable and Pinpoint carry the posting's **description** in
> the list response. BambooHR, SmartRecruiters and Rippling need a per-posting detail call for it
> (and, for BambooHR and SmartRecruiters, for the public URL too); BreezyHR has no keyless
> description at all and is deferred. Two further payload facts this table missed: Rippling
> repeats a job once per location, and Pinpoint publishes no post date. See ADR-0021, and the
> "As built" section at the end of this file.

## Tier 1 — cheap: same shape as Greenhouse/Lever/Ashby

Single keyless `GET`, JSON body, bare-token ref. Each is a ~30-line adapter plus a sanitized
fixture and tests — the `ingest/adapters/ashby.py` pattern verbatim, no new access method.

| ATS | Endpoint | Probe result | Companies |
|---|---|---|---:|
| **BambooHR** | `https://{ref}.bamboohr.com/careers/list` | 200, `{meta, result[]}` — four refs probed, 26 / 4 / 2 / 1 postings | **33** |
| **Recruitee** | `https://{ref}.recruitee.com/api/offers/` | 200, 167 offers | **6** |
| **Workable** | `https://apply.workable.com/api/v1/widget/accounts/{ref}?details=true` | 200 JSON | **5** |
| **Rippling** | `https://api.rippling.com/platform/api/ats/v1/board/{ref}/jobs` | 200, 28 jobs | 3 |
| **BreezyHR** | `https://{ref}.breezy.hr/json` | 200 JSON | 2 |
| **Pinpoint** | `https://{ref}.pinpointhq.com/postings.json` | 200, 37 postings | 2 |
| **SmartRecruiters** | `https://api.smartrecruiters.com/v1/companies/{ref}/postings?limit=100` | 200, 100 (capped — paginated) | 2 |

**53 companies** unlockable — a **43% increase** on the 123 boards now running. BambooHR alone
is 33, still the highest payoff-to-effort work in the project. SmartRecruiters is the only one
needing pagination (`limit`/`offset`).

Note Workable's **v1 widget** endpoint is the working one; the `api/v3/accounts/{ref}/jobs` path
that appears in newer docs returned 404 for every ref tried.

**The real work is mapping, not fetching.** Each platform names its fields differently, so the
per-adapter cost is the `RawPosting` mapping and its fixture, not the HTTP call.

## Tier 2 — reachable, but a different access method

| ATS | Finding | Companies |
|---|---|---:|
| **Workday** | ⚠️ **Superseded 2026-07-29 — see `workday-ref-discovery.md`.** This row read the 422 as "the endpoint is live"; re-probing showed **422 is the uniform reply to any unknown ref**, so it proves nothing and the site segment cannot be brute-forced. Still true: POST body, offset pagination, multi-segment ref (tenant / wd-number / site), and the list stores tenant only. Refs are recoverable by scraping the company website (~75% automatic). | **30** |
| **Eightfold** | 403 on `api/apply/v2/jobs` for both refs. Possibly fixable with correct domain param/headers; unproven. | 3 |
| **Jobvite, Oracle HCM, Paylocity** | **Untested — no usable `board_ref` stored** (blank refs). Cannot be judged until discovery captures one. | 11 |

## Tier 3 — not viable for V1 (stays inventory-only)

| ATS | Finding | Companies |
|---|---|---:|
| **Dayforce** | 404 on the candidate-portal API; the v2 path redirect-loops. Per-tenant portal, no public feed. | 10 |
| **Indeed** | Aggregator, ToS-restricted. Already parked (ADR-0013). | 10 |
| **Phenom** | 404; endpoint is per-tenant and inconsistent. | 9 |
| **ADP** | 500 from the public staffing endpoint. | 6 |
| **iCIMS** | HTML only, no keyless API — confirms ADR-0013's existing verdict. | 6 |
| **Teamtailor** | `jobs.json` 404; the public API requires a per-company `X-Api-Key`. | 6 |
| **JazzHR** | No JSON or RSS feed found (`/apply/jobs.json` 404, `/rss` returns HTML). API needs a key. | 5 |
| **SuccessFactors** | **401** — OData requires auth. Definitively not keyless. | 5 |
| **UKG** | 404 on the opportunities endpoint. | 5 |

---

## Suggested order

1. **BambooHR** — 33 companies, one small adapter.
2. **Recruitee + Workable** — 11 more, same pattern.
3. **Rippling, BreezyHR, Pinpoint, SmartRecruiters** — 9 more; do them together, they are nearly
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

---

## As built (2026-07-28) — what the second probe found

Every platform below was re-probed against a **second** live ref before its adapter was written,
which is what caught the differences from the table above.

| ATS | Description | Public URL | Post date | Other |
|---|---|---|---|---|
| BambooHR | detail only | detail only (`jobOpeningShareUrl`) | detail only | `state` and `province` used interchangeably; some boards fill only the country |
| Recruitee | list (`description` + `requirements`) | list | list | `published_at` is `"… UTC"`, not ISO 8601 |
| Workable | list (`details=true`) | list | list | account-level `description` is a company blurb — not the job's; unset fields are `""`, not null |
| Pinpoint | list, split across labelled blocks | list | **none published** | only `deadline_at` exists; landed date stays null |
| Rippling | detail (`description.role`) | list | detail | list emits **one row per job × location**, sharing a uuid |
| SmartRecruiters | detail (`jobAd.sections`) | detail (`postingUrl`) | list | only paginated source (`limit`/`offset`, `totalFound`); `fullLocation` leaves an empty slot for a missing region |

**BreezyHR — no keyless description exists.** Four paths tried on a live board:
`/{ref}.breezy.hr/json` (200, list without description), `/json/{id}` (**302 → `/`**),
the posting page `/p/{friendly_id}` (200 HTML, but a client-rendered shell whose markup still
contains `%BREADCRUMB_JOB_OPENINGS%`-style placeholders), and `api.breezy.hr/v3/company/{ref}/positions`
(**400**, key required). Deferred to V1.9 — see TODO.

**First full ingest (2026-07-28, 43 trial boards, 8 workers, 10m28s):** 1,324 postings, **0 with
an empty description**. Per source: SmartRecruiters 871, Recruitee 173, BambooHR 155, Workable 70,
Pinpoint 44, Rippling 11. One board skipped (a BambooHR ref that 302s to a non-JSON page).

Through the dbt DAG that became **336 gold postings** — SmartRecruiters 2, Recruitee 160,
BambooHR 119, Workable 33, Pinpoint 20, Rippling 2. The 871 → 2 collapse is the location gate
doing its job on a global board, and it is why a shared-host board that large is a poor trade
(~10 minutes of the run for two postings).
