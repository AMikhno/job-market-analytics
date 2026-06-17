import json

import responses

from ingest import pipeline
from ingest.adapters.lever import URL_TEMPLATE as LEVER_URL


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPELINE_TARGET", "dev")
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "j.duckdb"))
    monkeypatch.setenv("SUMMARY_PATH", str(tmp_path / "summary.json"))


@responses.activate
def test_run_happy_path_lands_rows_and_ops(tmp_path, monkeypatch, lever_payload) -> None:
    _env(monkeypatch, tmp_path)
    responses.add(responses.GET, LEVER_URL.format(slug="lever"), json=lever_payload)

    assert pipeline.run() == 0

    import duckdb

    con = duckdb.connect(str(tmp_path / "j.duckdb"))
    try:
        assert con.execute("select count(*) from raw_lever_jobs").fetchone()[0] == 1
        # greenhouse is inactive, but its raw table exists (empty) so dbt can build
        assert con.execute("select count(*) from raw_greenhouse_jobs").fetchone()[0] == 0
        # one ops row was recorded for the lever source
        assert con.execute("select count(*) from ops_ingest_runs").fetchone()[0] == 1
        status = con.execute("select status from ops_ingest_runs").fetchone()[0]
        assert status == "ok"
    finally:
        con.close()


@responses.activate
def test_zero_rows_warns_but_does_not_fail(tmp_path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)
    responses.add(responses.GET, LEVER_URL.format(slug="lever"), json=[])  # empty board

    rc = pipeline.run()

    assert rc == 0  # warn-only: never fails on low volume
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["warnings"] == ["lever"]
    assert summary["failures"] == []


@responses.activate
def test_error_source_hard_fails(tmp_path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)
    responses.add(responses.GET, LEVER_URL.format(slug="lever"), status=404)

    rc = pipeline.run()

    assert rc == 1  # a genuine error surfaces as a non-zero exit
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["failures"] == ["lever"]


@responses.activate
def test_one_bad_slug_skips_but_keeps_the_good_one(tmp_path, monkeypatch, lever_payload) -> None:
    companies = tmp_path / "companies.csv"
    companies.write_text(
        "company_name,source,company_slug,active,tier,notes\n"
        "GoodCo,lever,goodco,true,1,\n"
        "BadCo,lever,badco,true,1,\n"
    )
    _env(monkeypatch, tmp_path)
    monkeypatch.setenv("COMPANIES_CSV", str(companies))
    responses.add(responses.GET, LEVER_URL.format(slug="goodco"), json=lever_payload)
    responses.add(responses.GET, LEVER_URL.format(slug="badco"), status=404)

    rc = pipeline.run()

    assert rc == 0  # a 404 on one company doesn't fail the run
    import duckdb

    con = duckdb.connect(str(tmp_path / "j.duckdb"))
    try:
        assert con.execute("select count(*) from raw_lever_jobs").fetchone()[0] == 1
    finally:
        con.close()
    summary = json.loads((tmp_path / "summary.json").read_text())
    lever = next(s for s in summary["sources"] if s["source"] == "lever")
    assert "badco" in (lever["error"] or "")


def test_missing_company_files_is_caught_and_logged(tmp_path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)
    monkeypatch.setenv("COMPANIES_CSV", str(tmp_path / "nope.csv"))
    monkeypatch.setattr(pipeline, "EXAMPLE_COMPANIES", tmp_path / "also-nope.csv")

    rc = pipeline.run()

    assert rc == 1  # caught at the top, not an uncaught traceback
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["failures"] == ["__pipeline__"]


def test_catastrophic_error_returns_one_and_writes_summary(tmp_path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)

    def boom(_settings):
        raise RuntimeError("warehouse unreachable")

    monkeypatch.setattr(pipeline.storage, "ensure_raw_tables", boom)

    rc = pipeline.run()

    assert rc == 1
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["failures"] == ["__pipeline__"]
