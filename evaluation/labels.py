"""Relevance labels — the only ground truth this project has.

A label is one person's judgement of one posting, on four grades rather than
two. "Would I apply" hides the distinction that matters when a ranking is
wrong: a posting whose every requirement is met and a long shot worth a try are
both a yes, and a ranking that swaps them is not doing its job.

    best fit  the candidate's own work, tools and level
    fit       close to it: an equivalent tool, a gap, or a level that is not ideal
    stretch   a neighbouring discipline or a hard gap; worth applying to anyway
    drop      not worth an application
    skip      seen, deliberately not judged — kept out of the evaluation entirely

The judgement must be the labeller's own: typed by hand, or read in full by
someone else and then accepted row by row. It must never be the output of the
scorer under test, or of a model reading the same trimmed text that scorer
reads — that measures agreement with a predecessor, mistakes included.

A row names its posting by `job_key` or by `url`. A URL is what a person has in
front of them when they judge a posting; `evaluation/matching.py` resolves it.

Gitignored either way: a `job_key` is `(source, company, external_id)` and a URL
carries the board, so a label file is a list of company identifiers plus a
judgement about each.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

log = logging.getLogger(__name__)

ROOT: Final = Path(__file__).resolve().parents[1]
EXAMPLE_LABELS: Final = ROOT / "config" / "labels.example.csv"

BEST: Final = 3
FIT: Final = 2
STRETCH: Final = 1
DROP: Final = 0
GRADE_NAMES: Final = {BEST: "best fit", FIT: "fit", STRETCH: "stretch", DROP: "drop"}
# A posting at or above this grade is one a digest should surface near the top.
RELEVANT_FROM: Final = FIT

# The binary spellings are kept because the worksheet (`make labels-template`)
# and older files use them. `maybe` is a stretch: the labeller would try it, and
# did not call it a fit.
_GRADES: Final = {
    **dict.fromkeys(("best fit", "best_fit", "best", "yes", "y", "true", "1"), BEST),
    "fit": FIT,
    **dict.fromkeys(("stretch", "maybe", "m"), STRETCH),
    **dict.fromkeys(("drop", "no", "n", "false", "0"), DROP),
}
_KEY_COLUMNS: Final = ("job_key", "url")
_VERDICT_COLUMNS: Final = ("tier", "relevant", "verdict")


class Label(BaseModel):
    """One judgement about one posting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # A job_key, or the posting's URL.
    key: str
    # None is `skip`: the posting was seen and no judgement was made, so it must
    # be neither graded nor assumed a rejection.
    grade: int | None = Field(ge=DROP, le=BEST)

    @field_validator("key")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("key must not be blank")
        return cleaned

    @property
    def relevant(self) -> bool:
        return self.grade is not None and self.grade >= RELEVANT_FROM

    @property
    def is_url(self) -> bool:
        return self.key.lower().startswith(("http://", "https://"))


def parse_grade(raw: str) -> int | None:
    """Accept the spellings a person actually types, reject anything else.

    Treating an unrecognized value as `drop` would relabel a relevant posting
    as irrelevant — moving every metric in the flattering direction, invisibly.
    """
    value = raw.strip().lower()
    if value == "skip":
        return None
    if value not in _GRADES:
        raise ValueError(
            f"unrecognized verdict {raw!r}; use best fit/fit/stretch/drop/skip (or yes/maybe/no)"
        )
    return _GRADES[value]


def _first(row: dict[str, str | None], columns: tuple[str, ...]) -> str:
    return next((v.strip() for c in columns if (v := row.get(c)) and v.strip()), "")


def load_labels(path: str | Path) -> list[Label]:
    """Read a label CSV (`job_key` or `url`, then `tier`). Raises on a malformed row.

    Duplicates are rejected, not deduplicated: keeping one of two answers picks
    a judgement the labeller did not make.
    """
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = ROOT / resolved
    if not resolved.exists():
        raise FileNotFoundError(
            f"no labels at {resolved}. Copy {EXAMPLE_LABELS.name} and label real "
            "postings — without them the score cannot be evaluated, only computed."
        )

    # Strip leading comments and blanks: csv.DictReader takes the first line it
    # sees as the header, so a `#` note at the top of a hand-edited file would
    # become the column names and every row would parse as empty.
    lines = [
        line
        for line in resolved.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    labels: list[Label] = []
    seen: set[str] = set()
    for i, row in enumerate(csv.DictReader(lines), start=2):
        key = _first(row, _KEY_COLUMNS)
        if not key:
            continue
        # A blank verdict means "skipped", not "drop": the worksheet ships
        # pre-filled, and reading an unanswered row as a rejection would invent
        # judgements in the direction that flatters every ranking.
        verdict = _first(row, _VERDICT_COLUMNS)
        if not verdict:
            continue
        if key.lower() in seen:
            raise ValueError(f"{resolved}: duplicate key {key!r} (row {i})")
        seen.add(key.lower())
        labels.append(Label(key=key, grade=parse_grade(verdict)))
    if not labels:
        raise ValueError(f"{resolved} contains no labels")
    return labels
