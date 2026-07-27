"""Resolve a redacted identifier from a CI log back to a company.

Public CI logs digest company names and board_refs (see `shared/redact.py`), so
a failing board shows up as `board_ref=redacted:ad589ceb`. This re-hashes the
private company list locally to find the row that produced it:

    make whois REF=redacted:ad589ceb

Local use only. The whole point of the redaction is that this mapping exists
nowhere the list itself doesn't -- so never paste this output into an issue,
a PR, or anywhere else public.

Exits non-zero when nothing matches, which usually means the ref predates a
change to that row (the digest is over the current value) or the ref is from
a different list.
"""

from __future__ import annotations

import csv
import logging
import sys

from ingest.pipeline import _companies_path
from shared.config import Settings, get_settings
from shared.models import Company
from shared.redact import redact_ref

log = logging.getLogger("whois")


def resolve(ref: str, settings: Settings | None = None) -> list[Company]:
    """Every company whose name or board_ref digests to `ref`.

    Accepts the log form (`redacted:ad589ceb`) or the bare digest. Both fields
    are checked because either can appear in a log line, and a list can hold
    the same board under two names, so the result is a list.
    """
    settings = settings or get_settings()
    target = ref.removeprefix("redacted:").strip()
    matches: list[Company] = []
    with _companies_path(settings).open(newline="") as fh:
        for row in csv.DictReader(fh):
            company = Company.model_validate(row)
            digests = {
                redact_ref(company.company_name).removeprefix("redacted:"),
                redact_ref(company.board_ref).removeprefix("redacted:"),
            }
            if target in digests:
                matches.append(company)
    return matches


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        log.error("usage: make whois REF=redacted:xxxxxxxx")
        return 2
    matches = resolve(args[0])
    if not matches:
        log.error("no company in the list digests to %s", args[0])
        return 1
    for c in matches:
        log.info(
            "%s -> %s (%s: %s, active=%s)",
            args[0],
            c.company_name,
            c.source,
            c.board_ref,
            c.active,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
