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

_RAW_TABLE = {
    "greenhouse": "raw_greenhouse_jobs",
    "lever": "raw_lever_jobs",
    "ashby": "raw_ashby_jobs",
}

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

# ops.ingest_runs is queried directly, so it gets real types in BigQuery
# (raw stays all-STRING; bronze owns the casts).
_OPS_BQ_SCHEMA = [
    ("run_id", "STRING"),
    ("source", "STRING"),
    ("company_count", "INT64"),
    ("rows_fetched", "INT64"),
    ("status", "STRING"),
    ("started_at", "TIMESTAMP"),
    ("finished_at", "TIMESTAMP"),
    ("error", "STRING"),
]


def ensure_raw_tables(settings: Settings) -> None:
    """Idempotently provision the landing objects the pipeline writes to.

    Dev: empty DuckDB raw tables so dbt can build even with no ingest run.
    Prod: the *_raw / *_ops datasets and tables, with ingestion-time
    partitioning + expiry on raw so the append-only landing can't grow forever.
    No hand-run cloud setup: a fresh project provisions itself on first run.
    """
    if settings.is_prod:
        _ensure_bigquery_objects(settings)
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


def _ensure_bigquery_objects(settings: Settings) -> None:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project, location=settings.bq_location)
    raw_dataset = f"{settings.gcp_project}.{settings.bq_dataset}_raw"
    ops_dataset = f"{settings.gcp_project}.{settings.bq_dataset}_ops"
    for dataset_id in (raw_dataset, ops_dataset):
        dataset = bigquery.Dataset(dataset_id)
        dataset.location = settings.bq_location
        client.create_dataset(dataset, exists_ok=True)

    expiry_ms = settings.bq_raw_partition_expiry_days * 24 * 60 * 60 * 1000
    for table_name in _RAW_TABLE.values():
        table = bigquery.Table(
            f"{raw_dataset}.{table_name}",
            schema=[bigquery.SchemaField(c, "STRING") for c in RAW_COLUMNS],
        )
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY, expiration_ms=expiry_ms
        )
        client.create_table(table, exists_ok=True)

    ops_table = bigquery.Table(
        f"{ops_dataset}.ingest_runs",
        schema=[bigquery.SchemaField(name, kind) for name, kind in _OPS_BQ_SCHEMA],
    )
    client.create_table(ops_table, exists_ok=True)


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
        bq_schema=[(c, "STRING") for c in RAW_COLUMNS],
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
        bq_schema=_OPS_BQ_SCHEMA,
        settings=settings,
    )


def _write(
    rows: list[dict[str, object]],
    *,
    duckdb_table: str,
    bq_dataset: str,
    bq_table: str,
    bq_schema: list[tuple[str, str]],
    settings: Settings,
) -> None:
    if settings.is_prod:
        _write_bigquery(rows, bq_dataset, bq_table, bq_schema, settings)
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


def _write_bigquery(
    rows: list[dict[str, object]],
    dataset: str,
    table: str,
    schema: list[tuple[str, str]],
    settings: Settings,
) -> None:
    """Append via a batch load job — free, unlike streaming insert_rows_json."""
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project, location=settings.bq_location)
    table_id = f"{settings.gcp_project}.{dataset}.{table}"
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        schema=[bigquery.SchemaField(name, kind) for name, kind in schema],
        autodetect=False,
    )
    job = client.load_table_from_json(rows, table_id, job_config=job_config)
    job.result()  # blocks; raises on load errors
