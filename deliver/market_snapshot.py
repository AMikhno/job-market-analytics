"""Export the market aggregates to market.json for the project site (ADR-0029).

Runs at the end of a prod Ingest run, which is the only workflow that can reach
the warehouse. The JSON leaves as a workflow artifact; the Docs workflow, which
holds no warehouse credentials, downloads it and renders the charts. So nothing
here needs Pages permissions, and nothing in Docs needs BigQuery.

What may leave is decided by the `Market*` models in shared/models.py, not by the
SQL: each query selects exactly a model's declared fields, and validation
forbids anything else. Every row must also clear the company floor again -- dbt
already suppressed below it, and a failure here means that stopped working, so
the export fails rather than publishing.

Usage: python -m deliver.market_snapshot [path]   (default: market.json)
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from shared import storage
from shared.config import Settings, get_settings
from shared.models import (
    MARKET_MIN_COMPANIES,
    MarketAts,
    MarketDay,
    MarketRole,
    MarketSnapshot,
    MarketTech,
    MarketTenureStep,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("market_snapshot")


def _fetch[M: BaseModel](model: type[M], table: str, order_by: str, settings: Settings) -> list[M]:
    """Rows of one gold aggregate, selecting only the model's declared fields."""
    columns = ", ".join(model.model_fields)
    sql = f"select {columns} from {storage.gold_relation(table, settings)} order by {order_by}"
    return [model.model_validate(row) for row in storage.query_rows(sql, settings=settings)]


def build_snapshot(settings: Settings, *, now: datetime) -> MarketSnapshot:
    return MarketSnapshot(
        generated_at=now,
        min_companies=MARKET_MIN_COMPANIES,
        daily=_fetch(MarketDay, "agg_market_daily", "observed_on", settings),
        tenure=_fetch(MarketTenureStep, "agg_market_tenure", "days_on_board", settings),
        roles=_fetch(MarketRole, "agg_market_roles", "open_postings desc, role_family", settings),
        tech=_fetch(MarketTech, "agg_market_tech", "share_of_postings desc, tech", settings),
        ats=_fetch(MarketAts, "agg_market_ats", "open_postings desc, ats", settings),
    )


def run(argv: list[str]) -> int:
    path = Path(argv[0]) if argv else Path("market.json")
    snapshot = build_snapshot(get_settings(), now=datetime.now(UTC).replace(microsecond=0))
    path.write_text(snapshot.model_dump_json(indent=1))
    log.info(
        "market snapshot: %d days, %d tenure steps, %d roles, %d techs, %d ATS -> %s",
        len(snapshot.daily),
        len(snapshot.tenure),
        len(snapshot.roles),
        len(snapshot.tech),
        len(snapshot.ats),
        path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
