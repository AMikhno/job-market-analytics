"""Ingestion entrypoint.

Fetches every active source's companies, lands raw rows, and records one
ops.ingest_runs row per source. Failure model:
  * a bad/unreachable slug (e.g. 404) is a per-company WARNING — it is skipped
    and the other companies still run;
  * a source whose *every* slug failed is a HARD failure (non-zero exit);
  * an unexpected error anywhere is caught at the top, logged with a traceback,
    turned into a non-zero exit, and recorded in a failure summary;
  * a source returning < low_volume_threshold rows is a (non-failing) WARNING;
  * sustained staleness is caught separately by dbt source freshness.
"""

from __future__ import annotations

import csv
import json
import logging
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from ingest.adapters.base import SourceAdapter
from ingest.adapters.greenhouse import GreenhouseAdapter
from ingest.adapters.lever import LeverAdapter
from ingest.sources import SOURCES
from shared import storage
from shared.config import Settings, get_settings
from shared.http import build_session
from shared.models import IngestRun

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ingest")

ADAPTERS: dict[str, SourceAdapter] = {
    "greenhouse": GreenhouseAdapter(),
    "lever": LeverAdapter(),
}
ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_COMPANIES = ROOT / "config" / "companies.example.csv"


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


def load_slugs(source: str, settings: Settings | None = None) -> list[str]:
    """Read active company slugs for a source from the (private) company list."""
    settings = settings or get_settings()
    with _companies_path(settings).open() as fh:
        return [
            row["company_slug"]
            for row in csv.DictReader(fh)
            if row["source"] == source and row["active"].lower() == "true"
        ]


def run() -> int:
    """Top-level entry: never let an unexpected error escape unlogged."""
    settings = get_settings()
    try:
        return _run(settings)
    except Exception:
        log.exception("ingestion failed before completing")
        _write_summary(settings, run_id=None, failures=["__pipeline__"], warnings=[], runs=[])
        return 1


def _run(settings: Settings) -> int:
    session = build_session(settings.http_user_agent)
    run_id = uuid.uuid4().hex
    runs: list[IngestRun] = []
    storage.ensure_raw_tables(settings)  # so dbt can build even for empty sources

    for source in (s for s in SOURCES if s.active):
        slugs = load_slugs(source.adapter, settings)
        if not slugs:
            log.info("source=%s has no active companies; skipping", source.adapter)
            continue
        adapter = ADAPTERS[source.adapter]
        started = datetime.now(UTC)
        rows = 0
        failed_slugs: list[str] = []
        for slug in slugs:
            try:
                postings = adapter.fetch(session, slug)
                rows += storage.land(
                    postings, source=source.adapter, run_id=run_id, settings=settings
                )
            except Exception as exc:  # noqa: BLE001 - per-company; skip and keep going
                failed_slugs.append(slug)
                log.warning("source=%s slug=%s failed: %s", source.adapter, slug, exc)

        status, error = "ok", None
        if failed_slugs and len(failed_slugs) == len(slugs):
            status, error = "error", f"all slugs failed: {failed_slugs}"
        elif failed_slugs:
            error = f"skipped slugs: {failed_slugs}"  # status stays 'ok'
        runs.append(
            IngestRun(
                run_id=run_id,
                source=source.adapter,
                company_count=len(slugs),
                rows_fetched=rows,
                status=status,
                started_at=started,
                finished_at=datetime.now(UTC),
                error=error,
            )
        )
        log.info(
            "source=%s companies=%d ok=%d failed=%d rows=%d status=%s",
            source.adapter,
            len(slugs),
            len(slugs) - len(failed_slugs),
            len(failed_slugs),
            rows,
            status,
        )

    if not runs:
        log.warning("no active companies configured in %s", _companies_path(settings))

    storage.land_runs(runs, settings=settings)
    return _finalize(runs, settings)


def _finalize(runs: Sequence[IngestRun], settings: Settings) -> int:
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
    )
    for r in warnings:
        log.warning("low volume (warn-only): source=%s rows=%d", r.source, r.rows_fetched)
    if failures:
        log.error("hard failure: %s", [(r.source, r.error) for r in failures])
    return 1 if failures else 0


def _write_summary(
    settings: Settings,
    *,
    run_id: str | None,
    failures: list[str],
    warnings: list[str],
    runs: Sequence[IngestRun],
) -> None:
    summary = {
        "run_id": run_id,
        "failures": failures,
        "warnings": warnings,
        "sources": [
            {"source": r.source, "rows": r.rows_fetched, "status": r.status, "error": r.error}
            for r in runs
        ],
    }
    try:
        Path(settings.summary_path).write_text(json.dumps(summary, indent=2))
    except OSError:
        log.exception("could not write run summary to %s", settings.summary_path)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
