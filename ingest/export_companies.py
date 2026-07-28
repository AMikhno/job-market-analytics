"""Project the master company list down to what CI actually needs.

`config/companies.csv` is the master: every company you track, active or
inventory-only, with its website. CI does not need most of that -- the pipeline
reads only rows where `active=true` (see `load_companies`), so shipping the
inventory rows to the `COMPANIES_CSV_CONTENT` Actions variable publishes your
whole prospect list for no benefit, and pushes the variable toward GitHub's
48 KB-per-variable ceiling as the list grows.

This writes the same schema with the same columns -- it is a row filter, not a
reshape -- so `make validate-companies` checks the projection exactly as it
checks the master, and the projection stays a usable backup of the active list.

    make companies-variable

Writes to stdout by default so it can be piped straight into `gh variable set`.
"""

from __future__ import annotations

import csv
import logging
import sys
from typing import TextIO

from ingest.pipeline import _companies_path
from shared.config import Settings, get_settings
from shared.models import Company

__all__ = ["Company", "active_companies", "write_projection"]

log = logging.getLogger("export_companies")

FIELDNAMES = ["company_name", "source", "board_ref", "active", "tier", "website", "notes"]


def active_companies(settings: Settings | None = None) -> list[Company]:
    """Every active row of the master list, in file order."""
    settings = settings or get_settings()
    with _companies_path(settings).open(newline="") as fh:
        rows = [Company.model_validate(row) for row in csv.DictReader(fh)]
    return [c for c in rows if c.active]


def write_projection(companies: list[Company], out: TextIO) -> int:
    """Write the active rows as CSV; returns the number of rows written."""
    writer = csv.DictWriter(out, fieldnames=FIELDNAMES, lineterminator="\n")
    writer.writeheader()
    for c in companies:
        writer.writerow(
            {
                "company_name": c.company_name,
                "source": c.source,
                "board_ref": c.board_ref,
                "active": "true" if c.active else "false",
                "tier": c.tier,
                "website": c.website,
                "notes": c.notes,
            }
        )
    return len(companies)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    companies = active_companies()
    written = write_projection(companies, sys.stdout)
    # stderr so the CSV on stdout stays pipeable into `gh variable set`
    log.info("wrote %d active compan(ies)", written)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
