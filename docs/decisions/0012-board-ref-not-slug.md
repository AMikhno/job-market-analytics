# 0012 — Company boards are identified by `board_ref`, not a single slug

**Status:** accepted

The company list identifies each board with a `board_ref`: the ATS-specific path
fragment an adapter needs to reach one company's postings. For Greenhouse and Lever
that is a bare token (`boards.greenhouse.io/<ref>`), but several target ATS need more
than one parameter — Workday boards, for example, live at
`{tenant}.{instance}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs`. A one-token
`company_slug` column cannot express that, and V2 explicitly plans more sources.

So the contract is: **`board_ref` is opaque to the pipeline and interpreted by the
adapter that owns the source.** Greenhouse/Lever adapters format it straight into their
URL template; a future Workday adapter splits `tenant/instance/site` itself. URL
templates live only in the source registry (`ingest/sources.py`); adapters are
constructed from it, so there is a single source of truth.

The legacy `company_slug` CSV header is accepted as a validation alias so existing
private lists (including the GitHub Actions variable) keep working unchanged.

**Validation is adapter-owned too.** Because the ref's *shape* is source-specific, so is its
format check: each source in `ingest/sources.py` carries a `validate_board_ref` (default: a
bare board token — no slashes, spaces, or URL — which a multi-segment ATS like Workday
overrides). `load_companies` runs it on every active row before any fetch, so a pasted URL or
a stray slash fails loudly at load time instead of building a broken request and being
silently skipped as a 404. CSV parsing still passes multi-segment refs through untouched
(splitting is the adapter's job, not the loader's).
