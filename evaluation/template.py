"""Write a labelling worksheet from live gold (`make labels-template`).

Labelling is the one step nothing can automate — the whole point is that a
person decides — so the job here is to make it as close to mindless as possible:
one file, one column to fill, enough context per row to answer without opening
the posting most of the time.

Rows are drawn across the score range rather than from the top, which matters
more than it sounds. A worksheet of only high-scoring postings can measure
precision but never recall: if the scorer buried something relevant at rank 900,
a top-50 sample will never contain it, and the evaluation will report that
everything is fine.

Output goes to `config/labels.csv`, gitignored — a job_key is
(source, company, external_id), so a labelled file is a list of company
identifiers plus a judgement about each.
"""

from __future__ import annotations

import csv
import logging
import sys
from pathlib import Path
from typing import Final

from shared import storage
from shared.config import Settings, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("labels")

ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_OUT: Final = "config/labels.csv"
# Enough to be worth an evening and enough for P@k to mean something. Below
# ~100 the metric moves on one or two rows and reads as noise.
DEFAULT_SIZE: Final = 200

COLUMNS: Final = [
    "job_key",
    "relevant",
    "title",
    "company",
    "location",
    "geo_restriction",
    "fit_score",
    "match_score",
    "url",
]


def _sample(settings: Settings, size: int) -> list[dict[str, object]]:
    """Postings spread across the fit range, newest first within each band.

    Deterministic: same gold, same worksheet, so re-running does not reshuffle a
    file someone has started filling in.
    """
    sql = f"""
        select job_key, title, company, location, geo_restriction,
               fit_score, match_score, url
        from {storage.gold_table(settings)}
        qualify row_number() over (
            partition by coalesce(fit_score, 0)
            order by first_seen_at desc, job_key asc
        ) <= ?
        order by coalesce(fit_score, 0) desc, first_seen_at desc, job_key asc
    """
    # Six bands (unscored + 1..5), so per-band depth reaches roughly `size`.
    per_band = max(1, size // 6)
    return storage.query_rows(sql, params=[per_band], settings=settings)


def run(
    settings: Settings | None = None,
    out_path: str = DEFAULT_OUT,
    size: int = DEFAULT_SIZE,
) -> int:
    settings = settings or get_settings()
    target = Path(out_path)
    if not target.is_absolute():
        target = ROOT / target

    if target.exists():
        # Never overwrite: this file is hand-typed judgement, and regenerating it
        # is not worth the risk of destroying an evening's work.
        log.error("%s already exists — move or delete it first; refusing to overwrite", target)
        return 1

    rows = _sample(settings, size)
    if not rows:
        log.error("gold is empty; nothing to label")
        return 1

    with target.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({**{c: row.get(c) for c in COLUMNS}, "relevant": ""})

    log.info(
        "wrote %d rows to %s — fill the `relevant` column with yes or no, "
        "leave a row blank to skip it, then run `make evaluate`",
        len(rows),
        target,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
