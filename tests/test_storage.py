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


# --- BigQuery (prod) paths, against a fake client -----------------------------


class _FakeLoadJob:
    def result(self) -> None:
        return None


class _FakeBQClient:
    """Captures provisioning + load calls; asserts happen in the tests."""

    instances: list["_FakeBQClient"] = []

    def __init__(self, project: str | None = None, location: str | None = None) -> None:
        self.project = project
        self.location = location
        self.datasets: list[str] = []
        self.tables: list[object] = []
        self.loads: list[tuple[list[dict], str, object]] = []
        _FakeBQClient.instances.append(self)

    def create_dataset(self, dataset, exists_ok: bool = False):
        assert exists_ok, "provisioning must be idempotent"
        self.datasets.append(str(dataset.reference))
        return dataset

    def create_table(self, table, exists_ok: bool = False):
        assert exists_ok, "provisioning must be idempotent"
        self.tables.append(table)
        return table

    def load_table_from_json(self, rows, table_id, job_config=None):
        self.loads.append((list(rows), table_id, job_config))
        return _FakeLoadJob()


def _prod_settings() -> Settings:
    return Settings(_env_file=None, pipeline_target="prod", gcp_project="proj")


def _patch_client(monkeypatch) -> None:
    from google.cloud import bigquery

    _FakeBQClient.instances = []
    monkeypatch.setattr(bigquery, "Client", _FakeBQClient)


def test_ensure_raw_tables_provisions_bigquery(monkeypatch) -> None:
    _patch_client(monkeypatch)

    storage.ensure_raw_tables(_prod_settings())

    (client,) = _FakeBQClient.instances
    assert client.datasets == ["proj.jobs_raw", "proj.jobs_ops"]
    by_id = {str(t.reference): t for t in client.tables}
    assert set(by_id) == {
        "proj.jobs_raw.raw_greenhouse_jobs",
        "proj.jobs_raw.raw_lever_jobs",
        "proj.jobs_ops.ingest_runs",
    }
    for raw_id in ("proj.jobs_raw.raw_greenhouse_jobs", "proj.jobs_raw.raw_lever_jobs"):
        tp = by_id[raw_id].time_partitioning
        assert tp is not None and tp.expiration_ms == 400 * 24 * 60 * 60 * 1000
        assert [f.name for f in by_id[raw_id].schema] == storage.RAW_COLUMNS
    # ops table is typed, not all-STRING
    ops_types = {f.name: f.field_type for f in by_id["proj.jobs_ops.ingest_runs"].schema}
    assert ops_types["rows_fetched"] in ("INT64", "INTEGER")
    assert ops_types["started_at"] == "TIMESTAMP"


def test_land_uses_batch_load_job_on_prod(monkeypatch) -> None:
    _patch_client(monkeypatch)

    n = storage.land([_posting()], source="lever", run_id="r1", settings=_prod_settings())

    assert n == 1
    (client,) = _FakeBQClient.instances
    ((rows, table_id, job_config),) = client.loads
    assert table_id == "proj.jobs_raw.raw_lever_jobs"
    assert rows[0]["external_id"] == "x1"
    assert job_config.write_disposition == "WRITE_APPEND"
    assert job_config.autodetect is False
    assert [f.name for f in job_config.schema] == storage.RAW_COLUMNS
