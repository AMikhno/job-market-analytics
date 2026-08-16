"""Render the resume into a scoring prompt and land it for dbt to read.

Run before `dbt build` on a target that scores (`make scoring-prompt`). The
prompt travels through a table rather than `dbt --vars` because it is several KB
of multi-line text containing quotes, and shell-escaping that into a var is a
quoting bug waiting to happen; a table is also auditable after the fact.

Landing is append-only and skips when the newest row already carries this
`PROMPT_VERSION` and identical text, so running it twice does not accumulate
duplicate rows -- but a genuine wording change always lands a new row, because
that is what tells re-scoring the prompt moved.
"""

from __future__ import annotations

import logging
import sys

from shared import storage
from shared.config import Settings, get_settings
from shared.resume import PROMPT_VERSION, load_resume, render_prompt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("land_prompt")


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


def run(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    storage.ensure_scoring_prompt_table(settings)

    resume = load_resume(settings.resume_yaml)
    rendered = render_prompt(resume)

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
