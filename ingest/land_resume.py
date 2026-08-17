"""Land everything dbt needs from the private resume (`make land-resume`).

Two tables, for the two ways a posting is compared against the corpus:

* **scoring_prompt** — the rendered prompt the LLM scorer reads. Through a table
  rather than `dbt --vars` because it is several KB of quoted multi-line text.
  Append-only and versioned: a score is only comparable to another from the same
  wording, so the prompt that produced one has to stay readable.

* **resume_units** — one row per work bullet, for the embedding matcher.
  Replaced wholesale: an edited or deleted bullet must stop being matched.

Both live in the `_ops` dataset, and neither holds anything the resume does not.
"""

from __future__ import annotations

import hashlib
import logging
import sys

from shared import storage
from shared.config import Settings, get_settings
from shared.resume import (
    PROMPT_VERSION,
    EvidenceUnit,
    evidence_units,
    family_coverage,
    load_resume,
    render_prompt,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("land_resume")


def landed_version(rendered: str) -> str:
    """The version written beside every score: rubric version + text digest.

    `PROMPT_VERSION` alone tracks only the *rubric wording*, so replacing the
    resume would produce a different prompt under an unchanged version and
    int_jobs_scored would skip every posting -- the new corpus having no effect
    until someone ran a manual full refresh. Digesting the rendered text makes
    the invalidation self-enforcing instead.
    """
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:12]
    return f"{PROMPT_VERSION}.{digest}"


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


def _log_coverage(units: list[EvidenceUnit]) -> None:
    """Report bullets per role family, so a thin one is seen before it costs you.

    Untagged bullets still match -- the tag drives nothing at query time -- so a
    zero here is a reporting gap only if the work genuinely exists and was left
    untagged. Either way it is worth looking at.
    """
    counts = family_coverage(units)
    log.info("role-family coverage: %s", ", ".join(f"{k} {v}" for k, v in counts.items()))
    empty = [family for family, n in counts.items() if n == 0]
    if empty:
        log.warning(
            "no bullets tagged for %s — postings of that shape will match weakly",
            ", ".join(empty),
        )


def _land_units(settings: Settings, units: list[EvidenceUnit], version: str) -> int:
    """Replace the resume units. Returns how many were landed."""
    storage.ensure_resume_units_table(settings)
    storage.replace_resume_units(
        [
            {
                "unit_id": u.unit_id,
                "source": u.source,
                "text": u.text,
                # Joined rather than kept as an array: read by SQL on both
                # warehouses, and only one of them has arrays.
                "evidences": ", ".join(u.evidences),
                "prompt_version": version,
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
    version = landed_version(rendered)

    # Replaced every run whether or not the prompt moved: they are cheap, and a
    # stale unit keeps matching work the resume no longer claims.
    units = evidence_units(resume)
    landed = _land_units(settings, units, version)
    log.info("landed %d resume unit(s) for embedding", landed)
    _log_coverage(units)

    if _current(settings) == (version, rendered):
        log.info("scoring prompt already current at version %s; nothing landed", version)
        return 0

    storage.land_scoring_prompt(prompt_version=version, rendered_prompt=rendered, settings=settings)
    log.info(
        "landed scoring prompt version %s (%d chars) — scores from earlier "
        "versions are not comparable and will be re-scored",
        version,
        len(rendered),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
