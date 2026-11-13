"""Which ranking puts the postings worth applying to nearest the top?

An A/B on the *same* judged postings, so any difference is the ranking and not
the sample. Three rankings are graded: the keyword score, the LLM fit score and
embedding similarity — each as delivery would order it, with the keyword score
breaking ties, because a 1-5 rating alone leaves most of the order undecided.

Reads silver rather than gold. Gold holds live postings only, and a label
outlives the posting it judges: read from gold, a label file loses rows every
day until none are left to grade.

A ranking is preferred only when the bootstrap interval on its nDCG difference
from the keyword baseline excludes zero. A handful of labels can produce any
difference by chance, and "ranks better" printed over noise is worse than no
verdict.

With `--window`, every posting first seen in that span and not labelled counts
as `drop`. That is only true of a span whose postings were all read — it is
what turns a label file from a sample into a census, and so what makes recall
measurable at all.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import date
from typing import Final, NamedTuple

from evaluation.labels import DROP, GRADE_NAMES, Label, load_labels
from evaluation.matching import Candidate, resolve
from evaluation.metrics import (
    MISSING,
    Interval,
    Item,
    bootstrap,
    ndcg,
    precision_at,
    recall_at,
)
from shared import storage
from shared.config import Settings, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("evaluation")

# Sized to a digest, not to a dataset: how many postings a person actually reads
# before losing patience is what the ranking competes for.
CUTOFFS: Final = (5, 10, 20)
BASELINE: Final = "match_score"
_SHOWN_UNMATCHED: Final = 10


def _score(value: float | None) -> float:
    return MISSING if value is None else float(value)


# Each ranking as a sort key, larger first. Recency is deliberately absent: the
# digest uses it as a last tie-break, but it says nothing about relevance, so
# postings equal on these keys are scored as the tie they are.
RANKINGS: Final[dict[str, Callable[[Candidate], tuple[float, ...]]]] = {
    "match_score": lambda c: (float(c.match_score),),
    "fit_score": lambda c: (_score(c.fit_score), float(c.match_score)),
    "similarity": lambda c: (_score(c.similarity), float(c.match_score)),
}
_HAS_SCORE: Final[dict[str, Callable[[Candidate], bool]]] = {
    "match_score": lambda c: True,
    "fit_score": lambda c: c.fit_score is not None,
    "similarity": lambda c: c.similarity is not None,
}


class Result(NamedTuple):
    name: str
    scored: int
    ndcg: float | None
    precision_at: dict[int, float | None]
    recall_at: dict[int, float | None]


class Evaluation(NamedTuple):
    labels: int
    unmatched: list[str]
    judged: int
    assumed_drop: int
    grades: Counter[int]
    results: list[Result]
    intervals: dict[str, Interval]
    # (grade, fit_score or None) -> postings
    crosstab: Counter[tuple[int, int | None]]


def fetch_candidates(settings: Settings) -> list[Candidate]:
    """Every posting silver retains, with each ranking's score beside it.

    An out-of-range fit score is nulled exactly as gold does, so the evaluation
    grades the number delivery would have used.
    """
    sql = f"""
        select s.job_key, s.company, s.url, s.external_id, s.first_seen_at, s.match_score,
               case when sc.fit_score between 1 and 5 then sc.fit_score end as fit_score,
               m.similarity
        from {storage.silver_relation("silver_jobs", settings)} s
        left join {storage.silver_relation("int_jobs_scored", settings)} sc
            on s.content_hash = sc.content_hash
        left join {storage.silver_relation("int_jobs_matched", settings)} m
            on s.content_hash = m.content_hash
    """
    return [Candidate.model_validate(row) for row in storage.query_rows(sql, settings=settings)]


def _in_window(candidate: Candidate, window: tuple[date, date]) -> bool:
    seen = candidate.first_seen_at
    return seen is not None and window[0] <= seen.date() <= window[1]


def evaluate(
    labels: Sequence[Label],
    candidates: Sequence[Candidate],
    window: tuple[date, date] | None = None,
) -> Evaluation:
    """Grade every ranking on the judged postings."""
    resolution = resolve(labels, candidates)
    grades = dict(resolution.grades)
    assumed = 0
    if window is not None:
        for c in candidates:
            unjudged = c.job_key not in grades and c.job_key not in resolution.skipped
            if unjudged and _in_window(c, window):
                grades[c.job_key] = DROP
                assumed += 1

    judged = [c for c in candidates if c.job_key in grades]
    items = {
        name: [Item(grade=grades[c.job_key], key=key(c)) for c in judged]
        for name, key in RANKINGS.items()
    }
    results = [
        Result(
            name=name,
            scored=sum(1 for c in judged if _HAS_SCORE[name](c)),
            ndcg=ndcg(items[name]),
            precision_at={k: precision_at(items[name], k) for k in CUTOFFS},
            recall_at={k: recall_at(items[name], k) for k in CUTOFFS},
        )
        for name in RANKINGS
    ]
    # A ranking nothing was scored under is the baseline's own order with a
    # different name; an interval on it would compare the baseline to itself.
    running = {r.name: items[r.name] for r in results if r.scored or r.name == BASELINE}
    return Evaluation(
        labels=len(labels),
        unmatched=resolution.unmatched,
        judged=len(judged),
        assumed_drop=assumed,
        grades=Counter(grades[c.job_key] for c in judged),
        results=results,
        intervals=bootstrap(running, BASELINE) if judged else {},
        crosstab=Counter((grades[c.job_key], c.fit_score) for c in judged),
    )


def _cell(value: float | None) -> str:
    return f"{value:>6.2f}" if value is not None else f"{'n/a':>6}"


def format_report(ev: Evaluation) -> str:
    lines = [
        f"Labels:        {ev.labels}",
        f"Matched:       {ev.labels - len(ev.unmatched)}",
        f"Judged:        {ev.judged}"
        + (
            f" ({ev.assumed_drop} unlabelled in the window, counted as drop)"
            if ev.assumed_drop
            else ""
        ),
        "By grade:      "
        + ", ".join(f"{GRADE_NAMES[g]} {ev.grades[g]}" for g in sorted(GRADE_NAMES, reverse=True)),
    ]
    if ev.unmatched:
        lines.append(
            f"Unmatched:     {len(ev.unmatched)} — never ingested, expired from raw, or ambiguous"
        )
        lines += [f"    {key}" for key in ev.unmatched[:_SHOWN_UNMATCHED]]
        if len(ev.unmatched) > _SHOWN_UNMATCHED:
            lines.append(f"    … and {len(ev.unmatched) - _SHOWN_UNMATCHED} more")

    header = f"{'ranking':<13}{'scored':>7}{'nDCG':>7}"
    header += "".join(f"{f'P@{k}':>7}" for k in CUTOFFS) + "".join(
        f"{f'R@{k}':>7}" for k in CUTOFFS
    )
    lines += ["", header]
    for r in ev.results:
        if not r.scored:
            lines.append(f"{r.name:<13}{0:>7}   not running — nothing judged carries this score")
            continue
        lines.append(
            f"{r.name:<13}{r.scored:>7} {_cell(r.ndcg)}"
            + "".join(f" {_cell(r.precision_at[k])}" for k in CUTOFFS)
            + "".join(f" {_cell(r.recall_at[k])}" for k in CUTOFFS)
        )

    lines += ["", f"nDCG against {BASELINE} (95% bootstrap interval):"]
    if not ev.intervals:
        lines.append("  no verdict — no other ranking is running, or nothing judged is above drop")
    for name, interval in ev.intervals.items():
        lines.append(
            f"  {name:<13}{interval.delta:+.3f}  [{interval.low:+.3f}, {interval.high:+.3f}]"
            f"  {interval.verdict}"
        )

    scores: list[int | None] = [5, 4, 3, 2, 1, None]
    lines += ["", "fit_score by grade:", f"{'':<8}" + "".join(f"{s or 'none':>6}" for s in scores)]
    for grade in sorted(GRADE_NAMES, reverse=True):
        lines.append(
            f"{GRADE_NAMES[grade]:<8}" + "".join(f"{ev.crosstab[(grade, s)]:>6}" for s in scores)
        )
    return "\n".join(lines)


def run(
    settings: Settings | None = None,
    labels_path: str = "config/labels.csv",
    window: tuple[date, date] | None = None,
) -> int:
    settings = settings or get_settings()
    labels = load_labels(labels_path)
    ev = evaluate(labels, fetch_candidates(settings), window)
    if not ev.judged:
        # Not worth a traceback: every label names a posting silver no longer
        # holds, which is expected once a label file is older than raw retention.
        log.error("none of the %d labelled postings are in silver", len(labels))
        return 1
    print(format_report(ev))
    return 0


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--labels", default="config/labels.csv")
    parser.add_argument(
        "--window",
        nargs=2,
        type=date.fromisoformat,
        metavar=("FROM", "TO"),
        help="count unlabelled postings first seen in this span (inclusive, UTC) as drop",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":  # pragma: no cover
    args = _parse_args(sys.argv[1:])
    sys.exit(run(labels_path=args.labels, window=tuple(args.window) if args.window else None))
