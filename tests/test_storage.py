from datetime import UTC, datetime

from shared import storage
from shared.config import Settings
from shared.models import RawPosting


def _posting() -> RawPosting:
    return RawPosting(
        source="lever",
        company="acme",
        external_id="x1",
        title="Analytics Engineer",
        location="Ottawa",
        url="https://example/x1",
        description_html="<p>hi</p>",
        posted_or_updated_at=datetime(2026, 6, 1, tzinfo=UTC),
        raw={"id": "x1"},
    )


def test_rows_serializes_raw_and_timestamps() -> None:
    rows = storage._posting_rows([_posting()], run_id="r1")
    assert rows[0]["raw"] == '{"id": "x1"}'
    assert rows[0]["run_id"] == "r1"
    assert rows[0]["external_id"] == "x1"


def test_land_empty_returns_zero(tmp_path) -> None:
    settings = Settings(_env_file=None, duckdb_path=str(tmp_path / "j.duckdb"))
    assert storage.land([], source="lever", run_id="r1", settings=settings) == 0


def test_land_duckdb_writes_rows(tmp_path) -> None:
    import duckdb

    db = tmp_path / "j.duckdb"
    settings = Settings(_env_file=None, duckdb_path=str(db))
    n = storage.land([_posting()], source="lever", run_id="r1", settings=settings)
    assert n == 1
    con = duckdb.connect(str(db))
    try:
        count = con.execute("select count(*) from raw_lever_jobs").fetchone()[0]
    finally:
        con.close()
    assert count == 1


def test_land_runs_writes_ops_table(tmp_path) -> None:
    from datetime import datetime

    import duckdb

    from shared.models import IngestRun

    settings = Settings(_env_file=None, duckdb_path=str(tmp_path / "j.duckdb"))
    now = datetime(2026, 6, 1, tzinfo=UTC)
    runs = [
        IngestRun(
            run_id="r1",
            source="lever",
            company_count=1,
            rows_fetched=3,
            status="ok",
            started_at=now,
            finished_at=now,
            error=None,
        )
    ]
    storage.land_runs(runs, settings=settings)

    con = duckdb.connect(str(tmp_path / "j.duckdb"))
    try:
        row = con.execute("select source, rows_fetched, status from ops_ingest_runs").fetchone()
    finally:
        con.close()
    assert row == ("lever", "3", "ok")


def test_ensure_raw_tables_creates_both_empty(tmp_path) -> None:
    import duckdb

    settings = Settings(_env_file=None, duckdb_path=str(tmp_path / "j.duckdb"))
    storage.ensure_raw_tables(settings)
    con = duckdb.connect(str(tmp_path / "j.duckdb"))
    try:
        for tbl in ("raw_greenhouse_jobs", "raw_lever_jobs"):
            assert con.execute(f"select count(*) from {tbl}").fetchone()[0] == 0
    finally:
        con.close()
