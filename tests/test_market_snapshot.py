"""The market export is the one path from the warehouse to a public page.

So its guarantees are tested against a real (temp) DuckDB rather than mocks:
undeclared columns do not leave, a row below the company floor stops the export,
and the Python floor cannot drift from the dbt one.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
import yaml
from pydantic import ValidationError

from deliver import market_snapshot
from shared import storage
from shared.config import Settings
from shared.models import MARKET_MIN_COMPANIES, MarketSnapshot

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)

TABLES = {
    # Each table carries a `company` column the models do not declare: it must
    # never be selected, however the dbt model evolves.
    "agg_market_daily": (
        "observed_on DATE, boards BIGINT, open_postings BIGINT, new_postings BIGINT,"
        " companies BIGINT, company VARCHAR",
        [("2026-10-12", 130, 900, 40, 120, "leak"), ("2026-10-13", 131, 910, 35, 121, "leak")],
    ),
    "agg_market_tenure": (
        "days_on_board BIGINT, at_risk BIGINT, at_risk_companies BIGINT, closed BIGINT,"
        " survival DOUBLE, company VARCHAR",
        [(1, 500, 90, 50, 0.9, "leak"), (2, 400, 80, 40, 0.81, "leak")],
    ),
    "agg_market_roles": (
        "role_family VARCHAR, open_postings BIGINT, companies BIGINT, remote_postings BIGINT,"
        " hybrid_postings BIGINT, onsite_postings BIGINT, unstated_postings BIGINT,"
        " company VARCHAR",
        [("Analytics", 100, 30, 10, 5, 0, 85, "leak")],
    ),
    "agg_market_tech": (
        "tech VARCHAR, category VARCHAR, postings BIGINT, companies BIGINT,"
        " share_of_postings DOUBLE, company VARCHAR",
        [("dbt", "Transformation", 80, 35, 0.25, "leak"), ("SQL", "Language", 200, 60, 0.7, "x")],
    ),
    "agg_market_ats": (
        "ats VARCHAR, open_postings BIGINT, boards BIGINT, company VARCHAR",
        [("greenhouse", 6000, 56, "leak")],
    ),
}


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, duckdb_path=str(tmp_path / "jobs.duckdb"))


def _seed(settings: Settings, overrides: dict[str, list[tuple[object, ...]]] | None = None) -> None:
    con = duckdb.connect(settings.duckdb_path)
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS main_gold")
        for table, (columns, rows) in TABLES.items():
            con.execute(f"CREATE OR REPLACE TABLE main_gold.{table} ({columns})")
            for row in (overrides or {}).get(table, rows):
                con.execute(
                    f"INSERT INTO main_gold.{table} VALUES ({', '.join('?' * len(row))})",
                    list(row),
                )
    finally:
        con.close()


def test_snapshot_carries_only_declared_fields(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(settings)
    snapshot = market_snapshot.build_snapshot(settings, now=NOW)
    dumped = snapshot.model_dump_json()
    assert "leak" not in dumped and '"company"' not in dumped
    assert [d.open_postings for d in snapshot.daily] == [900, 910]


def test_snapshot_orders_rows_for_the_charts(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(settings)
    snapshot = market_snapshot.build_snapshot(settings, now=NOW)
    assert [t.tech for t in snapshot.tech] == ["SQL", "dbt"]  # most-asked first
    assert [s.days_on_board for s in snapshot.tenure] == [1, 2]


def test_a_row_below_the_company_floor_stops_the_export(tmp_path: Path) -> None:
    """dbt suppresses below the floor; if it ever stops, nothing is published."""
    settings = _settings(tmp_path)
    _seed(
        settings,
        {"agg_market_tech": [("Niche", "BI", 3, MARKET_MIN_COMPANIES - 1, 0.01, "x")]},
    )
    with pytest.raises(ValidationError, match="companies"):
        market_snapshot.build_snapshot(settings, now=NOW)


def test_run_writes_a_snapshot_the_site_can_read(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    _seed(settings)
    monkeypatch.setattr(market_snapshot, "get_settings", lambda: settings)
    out = tmp_path / "market.json"

    assert market_snapshot.run([str(out)]) == 0

    snapshot = MarketSnapshot.model_validate_json(out.read_text())
    assert snapshot.min_companies == MARKET_MIN_COMPANIES
    assert json.loads(out.read_text())["ats"] == [
        {"ats": "greenhouse", "open_postings": 6000, "boards": 56}
    ]


def test_python_floor_matches_the_dbt_var() -> None:
    """Two copies of one number: the dbt var suppresses, the Python one re-checks.
    If they drift, either the export rejects valid rows or the check goes slack."""
    project = yaml.safe_load((ROOT / "dbt" / "dbt_project.yml").read_text())
    assert project["vars"]["market_min_companies"] == MARKET_MIN_COMPANIES


def test_gold_relation_follows_the_target(tmp_path: Path) -> None:
    dev = _settings(tmp_path)
    prod = Settings(_env_file=None, pipeline_target="prod", gcp_project="p", bq_dataset="jobs")
    assert storage.gold_relation("agg_market_daily", dev) == "main_gold.agg_market_daily"
    assert storage.gold_relation("agg_market_daily", prod) == "p.jobs_gold.agg_market_daily"
