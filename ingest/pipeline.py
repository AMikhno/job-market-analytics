"""Ingestion entrypoint.

Fetches every active source's companies, lands raw rows, and records one
ops.ingest_runs row per source. Boards are fetched concurrently (politeness is
enforced per host, not globally — see shared/http.py) and their rows are landed
on the main thread, because the dev warehouse (DuckDB) is single-writer.

Failure model (unchanged by the parallel fetch):
  * a bad/unreachable board (e.g. 404) is a per-company WARNING — it is skipped
    and the other companies still run;
  * a source whose *every* board failed is a HARD failure (non-zero exit);
  * an unexpected error anywhere is caught at the top, logged with a traceback,
    turned into a non-zero exit, and recorded in a failure summary;
  * a source returning < low_volume_threshold rows is a (non-failing) WARNING;
  * a registered source with no active companies in the list is a (non-failing)
    WARNING -- it never runs, so it can never be low-volume, and without this it
    is invisible until its freshness gate errors ~30h later;
  * a source landing far below its own recent median is a (non-failing) WARNING.
    rows_fetched is a census of what is on the boards, not a count of new
    postings, so it is stable while boards are healthy -- which makes a sharp
    drop meaningful and an absolute floor nearly useless;
  * sustained staleness is caught separately by dbt source freshness.
"""

from __future__ import annotations

import csv
import logging
import sys
import uuid
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

from ingest.adapters.base import SourceAdapter
from ingest.sources import SOURCES, Source
from shared import storage
from shared.config import Settings, get_settings
from shared.http import HostRateLimiter, SessionPool
from shared.models import (
    Company,
    IngestRun,
    RawPosting,
    RunSummary,
    SourceSummary,
    VolumeDrop,
)
from shared.redact import redact_ref

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ingest")


def _label(value: str, settings: Settings) -> str:
    """Company identifier as it may appear in a (public) log line."""
    return redact_ref(value) if settings.redact_company_logs else value


# The source object (owner of the board_ref format rule, the URL templates and
# its adapter's construction) keyed by adapter name.
_SOURCE_BY_ADAPTER: dict[str, Source] = {s.adapter: s for s in SOURCES}
ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_COMPANIES = ROOT / "config" / "companies.example.csv"


def ensure_raw_tables(settings: Settings | None = None) -> None:
    """Provision one landing table per registered source (`make ensure-raw`)."""
    storage.ensure_raw_tables(settings or get_settings(), [s.adapter for s in SOURCES])


def _companies_path(settings: Settings) -> Path:
    """Resolve the company list: the private file if present, else the example.

    Falls back to the committed example (with a warning) so CI/clones run; raises
    only if neither file exists.
    """
    p = Path(settings.companies_csv)
    if not p.is_absolute():
        p = ROOT / p
    if p.exists():
        return p
    if EXAMPLE_COMPANIES.exists():
        log.warning(
            "company list not found at %s; using the example. "
            "Create that file with your real companies for a real run.",
            p,
        )
        return EXAMPLE_COMPANIES
    raise FileNotFoundError(f"no company list at {p} or {EXAMPLE_COMPANIES}")


def load_companies(source: str, settings: Settings | None = None) -> list[Company]:
    """Read the active companies for one source from the (private) company list.

    Every row is validated into a typed Company (a malformed list fails loudly,
    before anything is fetched); the legacy `company_slug` header still parses.
    Each active board_ref is then format-checked against its source's rule, so a
    pasted URL or a stray slash fails here rather than 404-ing mid-run.
    """
    settings = settings or get_settings()
    with _companies_path(settings).open(newline="") as fh:
        rows = [Company.model_validate(row) for row in csv.DictReader(fh)]
    active = [c for c in rows if c.source == source and c.active]
    src = _SOURCE_BY_ADAPTER.get(source)
    if src is not None:
        for c in active:
            src.validate_board_ref(c.board_ref)  # fail loudly before any fetch
    return active


def run() -> int:
    """Top-level entry: never let an unexpected error escape unlogged."""
    settings = get_settings()
    try:
        return _run(settings)
    except Exception:
        log.exception("ingestion failed before completing")
        _write_summary(settings, run_id=None, failures=["__pipeline__"], warnings=[], runs=[])
        return 1


@dataclass(frozen=True)
class _BoardResult:
    """One board's outcome, produced by a worker and consumed on the main thread."""

    source: str
    company: Company
    postings: list[RawPosting]
    error: Exception | None = None


def _fetch_board(
    adapter: SourceAdapter, sessions: SessionPool, source: str, company: Company
) -> _BoardResult:
    """Fetch one board, never raising: a failed board is data, not control flow.

    Runs on a worker thread, so it must not touch the warehouse (DuckDB takes a
    single writer) and must not raise — an escaping exception would surface at
    an unrelated `future.result()` and lose which board it belonged to.
    """
    try:
        return _BoardResult(source, company, adapter.fetch(sessions.get(), company.board_ref))
    except Exception as exc:  # noqa: BLE001 - per-company; recorded and reported below
        return _BoardResult(source, company, [], error=exc)


def _run(settings: Settings) -> int:
    run_id = uuid.uuid4().hex
    skipped: dict[str, list[str]] = {}  # source -> raw refs; redacted at summary time
    ensure_raw_tables(settings)  # so dbt can build even for empty sources

    sessions = SessionPool(settings.http_user_agent)
    limiter = HostRateLimiter(settings.fetch_min_interval_s)
    companies_by_source: dict[str, list[Company]] = {}
    unconfigured: list[str] = []
    for source in (s for s in SOURCES if s.active):
        companies = load_companies(source.adapter, settings)
        if not companies:
            # Warn, don't fail: shipping an adapter before its companies are in
            # the list is a legitimate order of work, and the committed example
            # list only covers a few sources. But this must not be silent --
            # a source that never runs still has a raw table with a freshness
            # gate on it, so the only other symptom is that gate erroring
            # ~30h later, pointing at the warehouse rather than at the list.
            log.warning(
                "source=%s is registered and active but has no active companies "
                "in the list; it will not run",
                source.adapter,
            )
            unconfigured.append(source.adapter)
            continue
        companies_by_source[source.adapter] = companies

    started = datetime.now(UTC)
    results = _fetch_all(companies_by_source, sessions, limiter, settings)
    finished = datetime.now(UTC)

    runs: list[IngestRun] = []
    for source_name, companies in companies_by_source.items():
        rows = 0
        failed_boards: list[str] = []
        for result in results[source_name]:
            if result.error is not None:
                # Raw ref: this list feeds IngestRun.error, which lands in the
                # private warehouse. Only the log line below is redacted.
                failed_boards.append(result.company.board_ref)
                log.warning(
                    "source=%s company=%s board_ref=%s failed: %s",
                    source_name,
                    _label(result.company.company_name, settings),
                    _label(result.company.board_ref, settings),
                    result.error,
                )
                continue
            rows += storage.land(
                result.postings, source=source_name, run_id=run_id, settings=settings
            )

        status, error = "ok", None
        if failed_boards and len(failed_boards) == len(companies):
            status, error = "error", f"all boards failed: {failed_boards}"
        elif failed_boards:
            error = f"skipped boards: {failed_boards}"  # status stays 'ok'
            # A partial skip used to be invisible: status stayed "ok" and only
            # low-volume sources reached the summary, so the digest could report
            # "all sources healthy" while a board silently dropped out of the
            # list. Carried out separately (redacted) so it is always reported.
            skipped[source_name] = list(failed_boards)
        runs.append(
            IngestRun(
                run_id=run_id,
                source=source_name,
                company_count=len(companies),
                rows_fetched=rows,
                status=status,
                # Boards from every source are fetched in one pool, so a source
                # no longer owns a contiguous slice of wall time; these are the
                # run's bounds, and the per-source cost is no longer separable.
                started_at=started,
                finished_at=finished,
                error=error,
            )
        )
        log.info(
            "source=%s companies=%d ok=%d failed=%d rows=%d status=%s",
            source_name,
            len(companies),
            len(companies) - len(failed_boards),
            len(failed_boards),
            rows,
            status,
        )

    if not runs:
        log.warning("no active companies configured in %s", _companies_path(settings))

    # Before landing this run, so the baseline is built from prior runs only.
    drops = _volume_drops(runs, settings)
    storage.land_runs(runs, settings=settings)
    return _finalize(runs, settings, skipped, unconfigured, drops)


def _volume_drops(runs: Sequence[IngestRun], settings: Settings) -> list[VolumeDrop]:
    """Sources that landed far below their own recent median.

    The absolute low-volume warning only fires at zero, which is the easy case.
    This is the hard one: a source that still returns plenty of rows but has
    quietly lost most of its boards. Compared against itself rather than a fixed
    number, because source sizes differ by orders of magnitude and the census is
    stable while boards are healthy (see Settings.volume_drop_ratio).
    """
    drops: list[VolumeDrop] = []
    for run in runs:
        if run.status != "ok":
            continue  # a hard failure is already reported; do not double-count
        try:
            history = storage.recent_row_counts(
                run.source, limit=settings.volume_history_runs, settings=settings
            )
        except Exception:
            # Deliberate catch: this is advisory monitoring layered on top of a
            # run whose rows are already landed. A warehouse hiccup reading the
            # ledger must not fail the ingest it is meant to watch -- the same
            # rule the digest and report_run steps follow. Logged with a
            # traceback so it is never silent.
            log.exception(
                "could not read run history for source=%s; skipping drop check", run.source
            )
            continue
        if len(history) < settings.volume_min_history:
            continue  # no trustworthy baseline yet
        baseline = int(median(history))
        if baseline > 0 and run.rows_fetched < baseline * settings.volume_drop_ratio:
            drops.append(VolumeDrop(source=run.source, rows=run.rows_fetched, baseline=baseline))
    return drops


def _interleave_by_host(
    work: list[tuple[SourceAdapter, str, Company]],
) -> list[tuple[SourceAdapter, str, Company]]:
    """Spread each host's boards evenly through the queue, keeping its own order.

    The pool consumes this list in order, so the order decides which hosts the
    workers contend on at any moment. Grouped by source, that is one host at a
    time and the run costs the sum of its sources.

    Spread *proportionally*, not round-robin. Round-robin gives every host one
    slot per cycle, which starves the host that has the most boards — precisely
    the one setting the wall time. Proportional keeps each host's share of the
    workers equal to its share of the boards, so the busiest host stays fed
    while the rest overlap it. A host with half the boards holds about half the
    pool; a host with one board takes one slot and gets out of the way.

    Position `(i + 0.5) / n` places a host's i-th board of n at that fraction of
    the queue. The sort is stable, so ties keep list order and a single-host
    list comes back untouched — no order makes one host faster than its own
    interval.
    """
    buckets: dict[str, list[int]] = defaultdict(list)
    for index, (_, source_name, company) in enumerate(work):
        buckets[_SOURCE_BY_ADAPTER[source_name].host_for(company.board_ref)].append(index)

    position = [0.0] * len(work)
    for indexes in buckets.values():
        for i, index in enumerate(indexes):
            position[index] = (i + 0.5) / len(indexes)
    return [work[i] for i in sorted(range(len(work)), key=lambda i: position[i])]


def _fetch_all(
    companies_by_source: dict[str, list[Company]],
    sessions: SessionPool,
    limiter: HostRateLimiter,
    settings: Settings,
) -> dict[str, list[_BoardResult]]:
    """Fetch every board of every source concurrently, grouped back by source.

    One pool across all sources, not one per source: the rate limiter is keyed by
    host, so the pool's only job is to keep enough boards in flight that the
    slowest host — not the sum of all of them — sets the wall time.

    That only holds if the boards in flight target *different* hosts, which is
    why the queue is interleaved by host before it is submitted. Grouped by
    source, every worker queues behind one host's interval and the sources run
    one after another: measured at 168 boards, the two largest shared-host
    sources took 12m20s and 3m14s back to back when they could have overlapped.
    Interleaved, the wall time is the slowest host rather than their sum.
    """
    results: dict[str, list[_BoardResult]] = {source: [] for source in companies_by_source}
    work: list[tuple[SourceAdapter, str, Company]] = []
    for source_name, companies in companies_by_source.items():
        adapter = _SOURCE_BY_ADAPTER[source_name].build(limiter)
        work.extend((adapter, source_name, company) for company in companies)
    if not work:
        return results
    work = _interleave_by_host(work)

    workers = max(1, min(settings.fetch_workers, len(work)))
    log.info("fetching %d board(s) with %d worker(s)", len(work), workers)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fetch") as pool:
        for result in pool.map(
            lambda item: _fetch_board(item[0], sessions, item[1], item[2]), work
        ):
            results[result.source].append(result)
    return results


def _finalize(
    runs: Sequence[IngestRun],
    settings: Settings,
    skipped: dict[str, list[str]] | None = None,
    unconfigured: Sequence[str] | None = None,
    drops: Sequence[VolumeDrop] | None = None,
) -> int:
    skipped = skipped or {}
    unconfigured = list(unconfigured or [])
    drops = list(drops or [])
    failures = [r for r in runs if r.status == "error"]
    warnings = [
        r for r in runs if r.status == "ok" and r.rows_fetched < settings.low_volume_threshold
    ]
    _write_summary(
        settings,
        run_id=runs[0].run_id if runs else None,
        failures=[r.source for r in failures],
        warnings=[r.source for r in warnings],
        runs=runs,
        skipped=skipped,
        unconfigured=unconfigured,
        drops=drops,
    )
    for d in drops:
        log.warning(
            "volume drop (warn-only): source=%s rows=%d vs baseline %d",
            d.source,
            d.rows,
            d.baseline,
        )
    for r in warnings:
        log.warning("low volume (warn-only): source=%s rows=%d", r.source, r.rows_fetched)
    for source, refs in skipped.items():
        # Redacted: this line is world-readable in the public Actions log. The
        # digest carries the same digests, and `make whois REF=…` resolves one
        # locally against the private list.
        log.warning(
            "skipped %d board(s) on source=%s: %s (resolve with `make whois REF=…`)",
            len(refs),
            source,
            ", ".join(redact_ref(ref) for ref in refs),
        )
    if failures:
        # r.error embeds the failed board_refs verbatim; keep it out of the log
        # and read it from ops.ingest_runs / the run summary instead.
        log.error(
            "hard failure: sources=%s (failed board_refs in ops.ingest_runs)",
            [r.source for r in failures],
        )
    return 1 if failures else 0


def _write_summary(
    settings: Settings,
    *,
    run_id: str | None,
    failures: list[str],
    warnings: list[str],
    runs: Sequence[IngestRun],
    skipped: dict[str, list[str]] | None = None,
    unconfigured: Sequence[str] | None = None,
    drops: Sequence[VolumeDrop] | None = None,
) -> None:
    skipped = skipped or {}
    summary = RunSummary(
        run_id=run_id,
        failures=failures,
        warnings=warnings,
        unconfigured=list(unconfigured or []),
        volume_drops=list(drops or []),
        sources=[
            SourceSummary(
                source=r.source,
                rows=r.rows_fetched,
                status=r.status,
                company_count=r.company_count,
                error=r.error,
                # redacted here, once, so every consumer of the summary is safe
                skipped_refs=[redact_ref(ref) for ref in skipped.get(r.source, [])],
            )
            for r in runs
        ],
    )
    try:
        Path(settings.summary_path).write_text(summary.model_dump_json(indent=2))
    except OSError:
        log.exception("could not write run summary to %s", settings.summary_path)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
