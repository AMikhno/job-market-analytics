"""Merge a discovery inventory into the master company list.

Staging used to be a bare `cp` over `config/companies.csv`, which was safe only
while the master held nothing the generator could not re-derive. It does now: a
hand-corrected board_ref (`REDACTED`, not `harness`), a deliberate
`active=false`, a note. Overwriting silently discards exactly the knowledge that
was most expensive to acquire.

The rule is conservative and predictable -- **the master always wins**:

  * a company not in the master is **added** from discovery;
  * a company already in the master **keeps its row**;
  * fields blank in the master and present in discovery are **filled** (this is
    how existing rows acquire a website without touching anything else);
  * a discovery row that disagrees with the master on `source`/`board_ref` is
    **reported as a conflict**, never applied. A conflict is real information --
    the company may have moved ATS -- so it is surfaced for a human rather than
    resolved by a rule that would be wrong half the time.

Companies are keyed by normalized name, which is the identity the list actually
uses (`REDACTED` and `REDACTED QNX` are deliberately separate rows).
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

from ingest.export_companies import FIELDNAMES
from shared.models import Company

log = logging.getLogger("merge_companies")

# Filled from discovery when the master leaves them blank. `source`/`board_ref`
# are deliberately absent: changing where a company's board lives is a conflict
# for a human, not a gap to backfill.
_FILLABLE = ("website", "notes")


def _key(name: str) -> str:
    return " ".join(name.split()).strip().lower()


@dataclass
class MergeResult:
    rows: list[Company]
    added: list[Company] = field(default_factory=list)
    filled: list[tuple[str, str]] = field(default_factory=list)  # (company, field)
    conflicts: list[tuple[str, str, str]] = field(default_factory=list)  # (company, master, new)

    @property
    def summary(self) -> str:
        return (
            f"{len(self.rows)} row(s): {len(self.added)} added, "
            f"{len(self.filled)} field(s) filled, {len(self.conflicts)} conflict(s)"
        )


def read_companies(path: Path) -> list[Company]:
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return [Company.model_validate(row) for row in csv.DictReader(fh)]


def merge(master: list[Company], incoming: list[Company]) -> MergeResult:
    """Layer a discovery inventory under the master list (master wins)."""
    by_key: dict[str, Company] = {_key(c.company_name): c for c in master}
    result = MergeResult(rows=list(master))

    for new in incoming:
        key = _key(new.company_name)
        current = by_key.get(key)
        if current is None:
            by_key[key] = new
            result.rows.append(new)
            result.added.append(new)
            continue

        updates: dict[str, str] = {}
        for name in _FILLABLE:
            if not getattr(current, name) and getattr(new, name):
                updates[name] = getattr(new, name)
                result.filled.append((current.company_name, name))
        if updates:
            merged = current.model_copy(update=updates)
            result.rows[result.rows.index(current)] = merged
            by_key[key] = merged
            current = merged

        if (new.source, new.board_ref) != (current.source, current.board_ref):
            result.conflicts.append(
                (
                    current.company_name,
                    f"{current.source}/{current.board_ref}",
                    f"{new.source}/{new.board_ref}",
                )
            )
    return result


def sort_key(c: Company) -> tuple[int, str, str]:
    """Active sources first, then inventory alphabetically -- the list's layout."""
    return (0 if c.active else 1, c.source, c.company_name.lower())


def write_companies(companies: list[Company], path: Path) -> int:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        for c in sorted(companies, key=sort_key):
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


def main(argv: list[str] | None = None) -> int:
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        log.error("usage: python -m ingest.merge_companies <master.csv> <incoming.csv>")
        return 2

    master_path, incoming_path = Path(args[0]), Path(args[1])
    result = merge(read_companies(master_path), read_companies(incoming_path))
    write_companies(result.rows, master_path)

    log.info("merged into %s -- %s", master_path, result.summary)
    for company, name in result.filled:
        log.info("  filled %s: %s", name, company)
    for company, current, new in result.conflicts:
        # Not applied: the master wins. Printed so a genuine ATS move is visible.
        log.warning("  conflict %s: master has %s, discovery found %s", company, current, new)
    if result.conflicts:
        log.warning(
            "%d conflict(s) left unapplied -- edit %s by hand to accept any of them",
            len(result.conflicts),
            master_path,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main())
