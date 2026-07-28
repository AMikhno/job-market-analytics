"""Pre-flight validation for the company list.

Run before pasting a list into the `COMPANIES_CSV_CONTENT` GitHub Actions
variable (or committing a private `config/companies.csv`): it parses every row
into a typed Company and format-checks each board_ref against its source's rule,
so a malformed list is caught here instead of 404-skipping mid-run.

    make validate-companies

Exits non-zero if any row is malformed, or if an **active** row's board_ref is
invalid for a registered source. Rows on ATS without an adapter yet
(inventory-only) parse but skip the format check — there is no rule to apply
until the adapter exists.

Inactive rows are checked but only *reported*, never failed: an inventory row
whose board has not been discovered yet legitimately carries a blank board_ref,
and it is never fetched. Failing on those would mean that shipping an adapter
for an ATS retroactively invalidated the inventory rows already sitting on it
— which is exactly what happened when Rippling and Workable gained adapters
while three Rippling rows still had no ref.
"""

from __future__ import annotations

import csv
import logging
import sys
from collections import Counter

from pydantic import ValidationError

from ingest.pipeline import _SOURCE_BY_ADAPTER, _companies_path
from shared.config import Settings, get_settings
from shared.models import Company

log = logging.getLogger("validate_companies")


def validate_company_list(settings: Settings | None = None) -> list[str]:
    """Return a list of human-readable problems (empty means the list is valid)."""
    return _scan(settings)[0]


def _scan(settings: Settings | None = None) -> tuple[list[str], list[str]]:
    """Return (problems, notices): failures on active rows, notes on inventory."""
    settings = settings or get_settings()
    problems: list[str] = []
    notices: list[str] = []
    counts: Counter[str] = Counter()

    with _companies_path(settings).open(newline="") as fh:
        # header is line 1, so the first data row is line 2
        for line_no, row in enumerate(csv.DictReader(fh), start=2):
            try:
                company = Company.model_validate(row)
            except ValidationError as exc:
                problems.append(f"row {line_no}: malformed ({exc.error_count()} error(s))")
                continue
            counts[company.source] += 1
            src = _SOURCE_BY_ADAPTER.get(company.source)
            if src is None:
                continue  # inventory-only ATS with no adapter yet — no rule to check
            try:
                src.validate_board_ref(company.board_ref)
            except ValueError as exc:
                # Only an *active* row can build a URL or hard-fail a run; an
                # inactive one is inventory waiting on discovery.
                target = problems if company.active else notices
                target.append(f"row {line_no} ({company.company_name}): {exc}")

    log.info("scanned %d row(s): %s", sum(counts.values()), dict(counts))
    return problems, notices


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    problems, notices = _scan()
    for notice in notices:
        log.warning("inactive row, not fetched: %s", notice)
    if problems:
        for problem in problems:
            log.error(problem)
        log.error("company list invalid: %d problem(s)", len(problems))
        return 1
    log.info("company list OK")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
