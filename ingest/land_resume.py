"""Land everything dbt needs from the private resume (`make land-resume`).

Three tables, for the three ways a posting is compared against the corpus:

* **scoring_prompt** — the rendered prompt the LLM scorer reads. Through a table
  rather than `dbt --vars` because it is several KB of quoted multi-line text.
  Append-only and versioned: a score is only comparable to another from the same
  wording, so the prompt that produced one has to stay readable.

* **resume_units** — one row per work bullet, one per other wording of a bullet
  and one per industry worked in, for the embedding matcher. Replaced wholesale:
  an edited or deleted bullet must stop being matched.

* **assessment_profile** — the candidate text the assessment model reads: the
  same corpus without the scoring rubric. Append-only and versioned, for the
  same reason.

All live in the `_ops` dataset, and none holds anything the resume does not.

An edit costs what it changes (ADR-0033). The two rendered texts are what a
model is billed to read, so only a change to one of them re-runs a model, and
each run says which it was. `--check` reports the same thing, and how many
postings it would re-run, without landing anything.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from collections.abc import Sequence
from typing import NamedTuple, cast

from shared import storage
from shared.config import Settings, get_settings
from shared.resume import (
    PROFILE_VERSION,
    PROMPT_VERSION,
    EvidenceUnit,
    evidence_units,
    family_coverage,
    industry_units,
    load_resume,
    render_profile,
    render_prompt,
    variant_units,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("land_resume")


def landed_version(rendered: str, base: str = PROMPT_VERSION) -> str:
    """The version written beside every score: rubric version + text digest.

    `PROMPT_VERSION` alone tracks only the *rubric wording*, so replacing the
    resume would produce a different prompt under an unchanged version and
    int_jobs_scored would skip every posting -- the new corpus having no effect
    until someone ran a manual full refresh. Digesting the rendered text makes
    the invalidation self-enforcing instead.
    """
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:12]
    return f"{base}.{digest}"


def _current(table: str, settings: Settings) -> tuple[str, str] | None:
    """The newest landed (prompt_version, rendered_prompt) in a table, or None."""
    rows = storage.query_rows(
        f"select prompt_version, rendered_prompt from {table} order by rendered_at desc limit 1",
        settings=settings,
    )
    if not rows:
        return None
    return str(rows[0]["prompt_version"]), str(rows[0]["rendered_prompt"])


class UnitChange(NamedTuple):
    """How the bullets moved against the last landing, counted by their text."""

    first_landing: bool
    bullets_added: int
    bullets_removed: int
    variants_added: int
    variants_removed: int


def unit_change(
    landed: Sequence[dict[str, object]],
    units: Sequence[EvidenceUnit],
    variants: Sequence[EvidenceUnit],
) -> UnitChange:
    """Compare this resume's bullets and variants with the landed ones.

    By text, not by id: a bullet's id is its position in its role, so inserting
    one renumbers every bullet after it, and an id comparison would report all
    of those as changed. An edited bullet therefore counts as one removed and
    one added, which is what it is to a model reading it.
    """
    # A table landed before `kind` or `variant_of` existed held bullets only.
    work = [row for row in landed if (row.get("kind") or "work") == "work"]
    old_bullets = {str(row["text"]) for row in work if not row.get("variant_of")}
    old_variants = {str(row["text"]) for row in work if row.get("variant_of")}
    new_bullets = {unit.text for unit in units}
    new_variants = {unit.text for unit in variants}
    return UnitChange(
        first_landing=not work,
        bullets_added=len(new_bullets - old_bullets),
        bullets_removed=len(old_bullets - new_bullets),
        variants_added=len(new_variants - old_variants),
        variants_removed=len(old_variants - new_variants),
    )


def change_report(change: UnitChange, *, prompt_changed: bool, profile_changed: bool) -> list[str]:
    """What this edit is and what it will re-run, as lines to log.

    The two flags decide the cost and the counts explain it: a model call is
    re-run only where the text that model reads changed.
    """
    lines: list[str] = []
    if change.first_landing:
        lines.append("resume: no earlier landing to compare with")
    else:
        lines.append(
            f"resume: {change.bullets_added} bullet(s) added, {change.bullets_removed} removed; "
            f"{change.variants_added} variant(s) added, {change.variants_removed} removed "
            "(an edited bullet counts as one of each)"
        )
    if not (prompt_changed or profile_changed):
        lines.append(
            "no text a model reads changed: nothing is re-scored or re-assessed, and "
            "similarity is recomputed from stored vectors on the next run"
        )
        return lines
    if not (change.first_landing or change.bullets_added or change.bullets_removed):
        lines.append(
            "no bullet was added or removed, so the change is outside them: the summary, "
            "seniority, constraints, skills, education, the order of the bullets, or the "
            "wording around them"
        )
    passes = [
        name
        for name, changed in (("re-scored", prompt_changed), ("re-assessed", profile_changed))
        if changed
    ]
    lines.append(
        f"open postings outside the drop tier are {' and '.join(passes)} on the next run; "
        "closed and dropped ones keep the result they have"
    )
    return lines


def repass_size(settings: Settings) -> tuple[int, int]:
    """How many posting texts a changed prompt would re-score, and a changed
    profile re-assess: the stored results that fall inside the re-pass scope.

    Read from the same view the scoring model's guard reads, so the number
    reported is the number billed rather than a second definition of it.
    """
    scope = storage.silver_relation("int_jobs_repass_scope", settings)
    scored = storage.silver_relation("int_jobs_scored", settings)
    assessed = storage.silver_relation("int_jobs_assessed", settings)
    rows = storage.query_rows(
        f"""
        select
            (
                select count(*)
                from {scope} r
                inner join {scored} s on r.content_hash = s.content_hash
            ) as rescored,
            (
                select count(*)
                from {scope} r
                inner join {assessed} a on r.content_hash = a.content_hash
            ) as reassessed
        """,
        settings=settings,
    )
    # query_rows is untyped at the cell level; a count is an integer on both.
    return int(cast(int, rows[0]["rescored"])), int(cast(int, rows[0]["reassessed"]))


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
                "kind": u.kind,
                "variant_of": u.variant_of,
            }
            for u in units
        ],
        settings=settings,
    )
    return len(units)


def run(settings: Settings | None = None, *, check: bool = False) -> int:
    settings = settings or get_settings()
    # Provisioned even for a check: dbt reads all three as sources, and an empty
    # table is what "nothing landed yet" looks like to the comparison below.
    storage.ensure_scoring_prompt_table(settings)
    storage.ensure_assessment_profile_table(settings)
    storage.ensure_resume_units_table(settings)

    resume = load_resume(settings.resume_yaml)
    rendered = render_prompt(resume)
    version = landed_version(rendered)
    profile = render_profile(resume)
    profile_version = landed_version(profile, PROFILE_VERSION)
    units = evidence_units(resume)
    variants = variant_units(resume)
    industries = industry_units(resume)

    prompt_changed = _current(storage.scoring_prompt_table(settings), settings) != (
        version,
        rendered,
    )
    profile_changed = _current(storage.assessment_profile_table(settings), settings) != (
        profile_version,
        profile,
    )
    # Before the units are replaced below: after that there is no earlier
    # landing left to compare with.
    change = unit_change(storage.landed_resume_units(settings), units, variants)
    for line in change_report(
        change, prompt_changed=prompt_changed, profile_changed=profile_changed
    ):
        log.info(line)

    if check:
        if prompt_changed or profile_changed:
            rescored, reassessed = repass_size(settings)
            if prompt_changed:
                log.info("landing this would re-score %d posting text(s)", rescored)
            if profile_changed:
                log.info("landing this would re-assess %d posting text(s)", reassessed)
        log.info("check only: nothing landed")
        return 0

    # Replaced every run whether or not the prompt moved: they are cheap, and a
    # stale unit keeps matching work the resume no longer claims.
    landed = _land_units(settings, units + variants + industries, version)
    log.info(
        "landed %d resume unit(s) for embedding (%d work, %d variant, %d industry)",
        landed,
        len(units),
        len(variants),
        len(industries),
    )
    if not industries:
        log.warning("no role names an industry — industry_similarity will be null")
    _log_coverage(units)

    if prompt_changed:
        storage.land_scoring_prompt(
            prompt_version=version, rendered_prompt=rendered, settings=settings
        )
        log.info(
            "landed scoring prompt version %s (%d chars) — scores from earlier "
            "versions are not comparable",
            version,
            len(rendered),
        )
    else:
        log.info("scoring prompt already current at version %s; nothing landed", version)

    if profile_changed:
        storage.land_assessment_profile(
            profile_version=profile_version, rendered_profile=profile, settings=settings
        )
        log.info("landed assessment profile version %s (%d chars)", profile_version, len(profile))
    else:
        log.info(
            "assessment profile already current at version %s; nothing landed", profile_version
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="report what this resume would change and re-run, and land nothing",
    )
    args = parser.parse_args(argv)
    return run(check=args.check)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
