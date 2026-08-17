"""Does the LLM score rank better than the keyword score?

An A/B on the *same* labelled postings: precision at k under `fit_score`
ordering against `match_score` ordering, so any difference is the ranking and
not the sample. Precision@k rather than accuracy because delivery is ordered,
never filtered (ADR-0020) — what matters is whether the good ones surface near
the top of an email, not whether every posting got the right number.
"""

from __future__ import annotations

import logging
import sys
from typing import Final, NamedTuple

from evaluation.labels import Label, load_labels
from shared import storage
from shared.config import Settings, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("evaluation")

# Sized to a digest, not to a dataset: how many postings a person actually reads
# before losing patience is what the ranking competes for. A cutoff at or above
# the candidate count is degenerate — every ordering holds the same rows — so
# `evaluate` reports it as None rather than as a tie.
CUTOFFS: Final = (5, 10, 20)


class Ranking(NamedTuple):
    name: str
    # job_keys, best first
    order: list[str]


class Result(NamedTuple):
    name: str
    # None at a cutoff that could not discriminate (k >= number of candidates).
    precision_at: dict[int, float | None]
    relevant_found_at: dict[int, int]


def _fetch_scored(settings: Settings, job_keys: list[str]) -> list[dict[str, object]]:
    """Gold rows for the labelled postings only."""
    placeholders = ", ".join("?" for _ in job_keys)
    sql = f"""
        select job_key, fit_score, match_score
        from {storage.gold_table(settings)}
        where job_key in ({placeholders})
    """
    return storage.query_rows(sql, params=list(job_keys), settings=settings)


def _rank(rows: list[dict[str, object]], key: str) -> list[str]:
    """Order job_keys by `key`, descending, unscored last.

    Ties and nulls resolve by job_key, so the metric cannot move between runs on
    identical data.
    """

    def sort_key(row: dict[str, object]) -> tuple[int, float, str]:
        value = row.get(key)
        if value is None:
            return (1, 0.0, str(row["job_key"]))
        return (0, -float(value), str(row["job_key"]))  # type: ignore[arg-type]

    return [str(r["job_key"]) for r in sorted(rows, key=sort_key)]


def evaluate(ranking: Ranking, labels: dict[str, bool]) -> Result:
    """Precision at each cutoff for one ranking.

    A cutoff that takes the whole candidate set scores None, not a number:
    identical by construction, and a tie there would read as a result.
    """
    precision: dict[int, float | None] = {}
    found: dict[int, int] = {}
    for k in CUTOFFS:
        top = ranking.order[:k]
        hits = sum(1 for key in top if labels.get(key, False))
        found[k] = hits
        precision[k] = hits / len(top) if len(ranking.order) > k else None
    return Result(name=ranking.name, precision_at=precision, relevant_found_at=found)


def _format(results: list[Result], labels: list[Label], covered: int) -> str:
    total_relevant = sum(1 for x in labels if x.relevant)
    lines = [
        f"Labelled postings: {len(labels)} ({total_relevant} relevant)",
        f"Found in gold:     {covered}",
        "",
        f"{'ranking':<14}" + "".join(f"  P@{k:<8}" for k in CUTOFFS),
    ]
    for r in results:
        cells = "".join(
            f"  {r.precision_at[k]:.2f} ({r.relevant_found_at[k]:>2})"
            if r.precision_at[k] is not None
            else f"  {'n/a':>4}     "
            for k in CUTOFFS
        )
        lines.append(f"{r.name:<14}{cells}")

    if len(results) != 2:
        return "\n".join(lines)

    usable = [
        k
        for k in CUTOFFS
        if results[0].precision_at[k] is not None and results[1].precision_at[k] is not None
    ]
    if not usable:
        lines += [
            "",
            "Verdict: none — every cutoff took the whole labelled set, so the two "
            "rankings cannot be told apart. Label more postings.",
        ]
        return "\n".join(lines)

    deltas = [
        (results[0].precision_at[k] or 0.0) - (results[1].precision_at[k] or 0.0) for k in usable
    ]
    total = sum(deltas)
    verdict = (
        "fit_score ranks better"
        if total > 0
        else "fit_score does NOT beat the keyword score"
        if total < 0
        else "the two rank equally well"
    )
    lines += ["", f"Verdict: {verdict} (sum of P@k deltas {total:+.2f} over k={usable})"]
    return "\n".join(lines)


def run(settings: Settings | None = None, labels_path: str = "config/labels.csv") -> int:
    settings = settings or get_settings()
    labels = load_labels(labels_path)
    by_key = {x.job_key: x.relevant for x in labels}

    rows = _fetch_scored(settings, list(by_key))
    if not rows:
        # Not worth a traceback: the labels name postings that have aged out of
        # gold, which is expected as a label file gets older.
        log.error("none of the %d labelled postings are in gold right now", len(labels))
        return 1

    results = [
        evaluate(Ranking("fit_score", _rank(rows, "fit_score")), by_key),
        evaluate(Ranking("match_score", _rank(rows, "match_score")), by_key),
    ]
    print(_format(results, labels, covered=len(rows)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
