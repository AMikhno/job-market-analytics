"""Human relevance labels — the only ground truth this project has.

They must be human. `docs/research/relevance-signals.md` records an LLM pass over
gold, and grading an LLM scorer against LLM labels measures agreement with a
predecessor including its mistakes: a scorer that reproduced every one of that
pass's errors would score perfectly. So these come from a person or they are
worthless.

Private, and gitignored: a `job_key` is `(source, company, external_id)`, so a
label file is a list of company identifiers plus a judgement about each — both
halves of what the company list is private to protect.

Binary, not 1-5. "Would I apply to this?" is a question with a stable answer;
"is this a 3 or a 4?" is not, and an unstable label makes the metric noise.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, field_validator

log = logging.getLogger(__name__)

ROOT: Final = Path(__file__).resolve().parents[1]
EXAMPLE_LABELS: Final = ROOT / "config" / "labels.example.csv"

_TRUE: Final = frozenset({"true", "yes", "y", "1"})
_FALSE: Final = frozenset({"false", "no", "n", "0"})


class Label(BaseModel):
    """One human judgement about one posting."""

    model_config = ConfigDict(extra="forbid")

    job_key: str
    relevant: bool

    @field_validator("job_key")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("job_key must not be blank")
        return cleaned


def _parse_relevant(raw: str) -> bool:
    """Accept the spellings a human actually types, reject anything else.

    Silently treating an unrecognized value as False would quietly relabel a
    relevant posting as irrelevant, which moves every metric in the flattering
    direction — fewer false positives, and nothing to notice.
    """
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"unrecognized relevant value {raw!r}; use yes/no")


def load_labels(path: str | Path) -> list[Label]:
    """Read a label CSV (`job_key,relevant`). Raises on a malformed row.

    Duplicates are rejected rather than deduplicated: two rows for one posting
    means the file was edited twice with different answers, and silently keeping
    one of them picks a judgement the labeller did not make.
    """
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = ROOT / resolved
    if not resolved.exists():
        raise FileNotFoundError(
            f"no labels at {resolved}. Copy {EXAMPLE_LABELS.name} and label real "
            "postings — without them the score cannot be evaluated, only computed."
        )

    # Strip leading comments and blanks before the header. csv.DictReader takes
    # the first line it sees as the header, so a `#` note at the top of a
    # hand-edited file would silently become the column names and every row
    # would then parse as empty.
    lines = [
        line
        for line in resolved.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    labels: list[Label] = []
    seen: set[str] = set()
    for i, row in enumerate(csv.DictReader(lines), start=2):
        key = (row.get("job_key") or "").strip()
        if not key:
            continue
        if key in seen:
            raise ValueError(f"{resolved}: duplicate job_key {key!r} (row {i})")
        seen.add(key)
        labels.append(Label(job_key=key, relevant=_parse_relevant(row.get("relevant") or "")))
    if not labels:
        raise ValueError(f"{resolved} contains no labels")
    return labels
