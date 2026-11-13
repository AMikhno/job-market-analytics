"""Resolve labels onto warehouse postings.

A label keyed by `job_key` resolves by equality. One keyed by URL needs more
care, because the URL a person copied is whatever the browser settled on after
redirects — for several ATS a company-hosted page rather than the address the
API returned. The posting id survives that rewrite, so a URL that does not
match exactly is matched on the id it carries.

Nothing is guessed. A label that matches no posting, or more than one, is
returned as unmatched and reported: silently dropping it would shrink the
evaluation set without saying so, and picking one of several candidates would
attach a judgement to a posting nobody judged.
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Sequence
from datetime import datetime
from typing import Final, NamedTuple

from pydantic import BaseModel, ConfigDict

from evaluation.labels import Label

_SEPARATORS: Final = re.compile(r"[/?&=]")
# Below this length an id is a counter, not an identifier: several boards hold a
# posting numbered 48, so a short id only counts when the URL also names the board.
_MIN_GLOBAL_ID: Final = 5


class Candidate(BaseModel):
    """One posting as the evaluation sees it: identity, and each ranking's inputs."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    job_key: str
    company: str | None = None
    url: str | None = None
    external_id: str | None = None
    first_seen_at: datetime | None = None
    match_score: int = 0
    fit_score: int | None = None
    similarity: float | None = None


class Resolution(NamedTuple):
    # job_key -> grade, for every graded label that found exactly one posting
    grades: dict[str, int]
    # job_keys of postings labelled `skip`: seen, deliberately not judged
    skipped: set[str]
    # label keys that found none, or several
    unmatched: list[str]


def normalise_url(url: str) -> str:
    """Comparable form of a posting URL.

    Percent-decoded because a board name with a space arrives encoded from one
    place and decoded from another; lowercased because some board names are
    display names whose case is not stable; fragment dropped because it never
    reaches a server.
    """
    return urllib.parse.unquote(url).strip().lower().split("#", 1)[0].rstrip("/")


def url_tokens(url: str) -> set[str]:
    """Path segments and query values: wherever an ATS puts a posting id."""
    return {token for token in _SEPARATORS.split(normalise_url(url)) if token}


def resolve(labels: Sequence[Label], candidates: Sequence[Candidate]) -> Resolution:
    """Attach each label to the one posting it names.

    Two labels landing on one posting are accepted when they agree and rejected
    when they do not — the same rule the loader applies to a duplicated key.
    """
    by_key = {c.job_key: c for c in candidates}
    by_url: dict[str, list[Candidate]] = {}
    by_id: dict[str, list[Candidate]] = {}
    for c in candidates:
        if c.url:
            by_url.setdefault(normalise_url(c.url), []).append(c)
        if c.external_id:
            by_id.setdefault(c.external_id.strip().lower(), []).append(c)

    verdicts: dict[str, int | None] = {}
    unmatched: list[str] = []
    for label in labels:
        hits = _find(label, by_key, by_url, by_id)
        if len(hits) != 1:
            unmatched.append(label.key)
            continue
        job_key = hits[0].job_key
        if verdicts.get(job_key, label.grade) != label.grade:
            raise ValueError(f"two labels disagree about one posting: {label.key!r}")
        verdicts[job_key] = label.grade
    return Resolution(
        grades={k: g for k, g in verdicts.items() if g is not None},
        skipped={k for k, g in verdicts.items() if g is None},
        unmatched=unmatched,
    )


def _find(
    label: Label,
    by_key: dict[str, Candidate],
    by_url: dict[str, list[Candidate]],
    by_id: dict[str, list[Candidate]],
) -> list[Candidate]:
    if not label.is_url:
        hit = by_key.get(label.key)
        return [hit] if hit else []
    exact = by_url.get(normalise_url(label.key), [])
    if exact:
        return exact
    url = normalise_url(label.key)
    found = {
        c.job_key: c
        for token in url_tokens(label.key)
        for c in by_id.get(token, [])
        if len(token) >= _MIN_GLOBAL_ID or (c.company and c.company.strip().lower() in url)
    }
    return list(found.values())
