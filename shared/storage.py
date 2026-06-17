"""Land rows into the target warehouse (DuckDB dev / BigQuery prod).

One generic writer backs both the raw posting tables and the ops.ingest_runs
metadata table, so the DuckDB/BigQuery branching lives in exactly one place.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from shared.config import Settings
from shared.models import IngestRun, RawPosting

_RAW_TABLE = {"greenhouse": "raw_greenhouse_jobs", "lever": "raw_lever_jobs"}

# Canonical raw column order (must match the dict built in _posting_rows).
RAW_COLUMNS = [
    "source",
    "company",
    "external_id",
    "title",
    "location",
    "remote_policy",
    "department",
    "employment_type",
    "url",
    "description_html",
    "posted_or_updated_at",
    "raw",
    "ingested_at",
    "run_id",
]


def ensure_raw_tables(settings: Settings) -> None:
    """Create empty raw tables so dbt can build even for sources with no rows.

    Dev (DuckDB) only; in prod the raw tables are created during cloud setup.
    """
    if settings.is_prod:
        return
    import duckdb

    Path(settings.duckdb_path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(settings.duckdb_path)
    try:
        cols = ", ".join(f"{c} VARCHAR" for c in RAW_COLUMNS)
        for table in _RAW_TABLE.values():
            con.execute(f"CREATE TABLE IF NOT EXISTS {table} ({cols})")
    finally:
        con.close()


def _posting_rows(postings: Sequence[RawPosting], run_id: str) -> list[dict[str, object]]:
    now = datetime.now(UTC).isoformat()
    return [
        {
            "source": p.source,
            "company": p.company,
            "external_id": p.external_id,
            "title": p.title,
            "location": p.location,
            "remote_policy": p.remote_policy,
            "department": p.department,
            "employment_type": p.employment_type,
            "url": p.url,
            "description_html": p.description_html,
            "posted_or_updated_at": (
                p.posted_or_updated_at.isoformat() if p.posted_or_updated_at else None
            ),
            "raw": json.dumps(p.raw),
            "ingested_at": now,
            "run_id": run_id,
        }
        for p in postings
    ]


def _run_rows(runs: Sequence[IngestRun]) -> list[dict[str, object]]:
    return [
        {
            "run_id": r.run_id,
            "source": r.source,
            "company_count": r.company_count,
            "rows_fetched": r.rows_fetched,
            "status": r.status,
            "started_at": r.started_at.isoformat(),
            "finished_at": r.finished_at.isoformat(),
            "error": r.error,
        }
        for r in runs
    ]


def land(postings: Sequence[RawPosting], *, source: str, run_id: str, settings: Settings) -> int:
    """Append postings to the source's raw table. Returns rows written."""
    rows = _posting_rows(postings, run_id)
    if not rows:
        return 0
    _write(
        rows,
        duckdb_table=_RAW_TABLE[source],
        bq_dataset=f"{settings.bq_dataset}_raw",
        bq_table=_RAW_TABLE[source],
        settings=settings,
    )
    return len(rows)


def land_runs(runs: Sequence[IngestRun], *, settings: Settings) -> None:
    """Persist one ops.ingest_runs row per source per run."""
    rows = _run_rows(runs)
    if not rows:
        return
    _write(
        rows,
        duckdb_table="ops_ingest_runs",
        bq_dataset=f"{settings.bq_dataset}_ops",
        bq_table="ingest_runs",
        settings=settings,
    )


def _write(
    rows: list[dict[str, object]],
    *,
    duckdb_table: str,
    bq_dataset: str,
    bq_table: str,
    settings: Settings,
) -> None:
    if settings.is_prod:
        _write_bigquery(rows, bq_dataset, bq_table, settings)
    else:
        _write_duckdb(rows, duckdb_table, settings)


def _write_duckdb(rows: list[dict[str, object]], table: str, settings: Settings) -> None:
    import duckdb  # local import so prod runs don't need it loaded

    Path(settings.duckdb_path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(settings.duckdb_path)
    try:
        cols = ", ".join(f"{k} VARCHAR" for k in rows[0])
        con.execute(f"CREATE TABLE IF NOT EXISTS {table} ({cols})")
        placeholders = ", ".join("?" for _ in rows[0])
        con.executemany(
            f"INSERT INTO {table} VALUES ({placeholders})",
            [[None if v is None else str(v) for v in r.values()] for r in rows],
        )
    finally:
        con.close()


def _write_bigquery(  # pragma: no cover - requires live BigQuery
    rows: list[dict[str, object]], dataset: str, table: str, settings: Settings
) -> None:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project, location=settings.bq_location)
    table_id = f"{settings.gcp_project}.{dataset}.{table}"
    errors = client.insert_rows_json(table_id, rows)
    if errors:
        raise RuntimeError(f"BigQuery insert errors: {errors}")
