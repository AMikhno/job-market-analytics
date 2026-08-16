"""Land everything dbt needs from the private resume (`make land-resume`).

Two tables, for the two ways a posting is compared against the corpus:

* **scoring_prompt** — the rendered prompt the LLM scorer reads. It travels
  through a table rather than `dbt --vars` because it is several KB of
  multi-line text containing quotes, and shell-escaping that into a var is a
  quoting bug waiting to happen; a table is also auditable after the fact.
  Append-only and versioned, because a score is only comparable to another from
  the same wording, so the prompt that produced a score has to stay readable.

* **resume_units** — one row per work bullet, for the embedding matcher.
  Replaced wholesale rather than appended: an edited or deleted bullet must stop
  being matched against.

Neither table contains anything the resume file does not, and both live in the
`_ops` dataset rather than the repo.
"""

from __future__ import annotations

import logging
import sys

from shared import storage
from shared.config import Settings, get_settings
from shared.resume import PROMPT_VERSION, evidence_units, load_resume, render_prompt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("land_resume")


def _current(settings: Settings) -> tuple[str, str] | None:
    """The newest landed (prompt_version, rendered_prompt), or None if empty."""
    table = storage.scoring_prompt_table(settings)
    rows = storage.query_rows(
        f"select prompt_version, rendered_prompt from {table} order by rendered_at desc limit 1",
        settings=settings,
    )
    if not rows:
        return None
    return str(rows[0]["prompt_version"]), str(rows[0]["rendered_prompt"])


def _land_units(settings: Settings, resume: object) -> int:
    """Replace the resume units. Returns how many were landed."""
    units = evidence_units(resume)  # type: ignore[arg-type]
    storage.ensure_resume_units_table(settings)
    storage.replace_resume_units(
        [
            {
                "unit_id": u.unit_id,
                "source": u.source,
                "text": u.text,
                # Joined rather than kept as an array: the units table is read by
                # SQL on both warehouses, and only one of them has arrays.
                "evidences": ", ".join(u.evidences),
                "prompt_version": PROMPT_VERSION,
            }
            for u in units
        ],
        settings=settings,
    )
    return len(units)


def run(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    storage.ensure_scoring_prompt_table(settings)

    resume = load_resume(settings.resume_yaml)
    rendered = render_prompt(resume)

    # Units are replaced every run regardless of whether the prompt moved: they
    # are cheap, and a stale unit keeps matching postings against work the
    # resume no longer claims.
    landed = _land_units(settings, resume)
    log.info("landed %d resume unit(s) for embedding", landed)

    if _current(settings) == (PROMPT_VERSION, rendered):
        log.info("scoring prompt already current at version %s; nothing landed", PROMPT_VERSION)
        return 0

    storage.land_scoring_prompt(
        prompt_version=PROMPT_VERSION, rendered_prompt=rendered, settings=settings
    )
    log.info(
        "landed scoring prompt version %s (%d chars) — scores from earlier "
        "versions are not comparable and will be re-scored",
        PROMPT_VERSION,
        len(rendered),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
