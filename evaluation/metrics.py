"""Ranking metrics that do not reward an arbitrary tie-break.

A 1-5 score puts dozens of postings on the same rung. Any single ordering of a
tied group is a coin toss, and a metric computed on one such ordering reports
the toss as if it were the ranking. Every metric here is therefore an
*expectation over the orderings a ranking leaves open*: a tied group shares the
average weight of the positions it occupies (McSherry & Najork, 2008). A
ranking with no ties scores exactly as it would under the plain definition.

All three metrics are that one calculation with a different position weight:
nDCG discounts by log rank and uses the full grade; precision and recall at k
weight the top k equally and count a posting as relevant or not.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping, Sequence
from itertools import groupby
from typing import Final, NamedTuple

from evaluation.labels import RELEVANT_FROM

MISSING: Final = float("-inf")


class Item(NamedTuple):
    """One judged posting under one ranking."""

    grade: int
    # Sort key, larger first. A missing score is MISSING, so it sorts last.
    key: tuple[float, ...]


class Interval(NamedTuple):
    """A paired difference in nDCG against the baseline, with its 95% interval."""

    delta: float
    low: float
    high: float

    @property
    def verdict(self) -> str:
        if self.low > 0:
            return "better"
        if self.high < 0:
            return "worse"
        return "not distinguishable"


def _discount(position: int) -> float:
    return 1.0 / math.log2(position + 1)


def _relevant(grade: int) -> float:
    return 1.0 if grade >= RELEVANT_FROM else 0.0


def _expected(
    items: Sequence[Item], weight: Callable[[int], float], gain: Callable[[int], float]
) -> float:
    """Sum of gain x position weight, averaged over every order ties allow."""
    ordered = sorted(items, key=lambda item: item.key, reverse=True)
    total = 0.0
    position = 1
    for _, group in groupby(ordered, key=lambda item: item.key):
        members = list(group)
        share = sum(weight(p) for p in range(position, position + len(members))) / len(members)
        total += share * sum(gain(m.grade) for m in members)
        position += len(members)
    return total


def ndcg(items: Sequence[Item]) -> float | None:
    """Normalized discounted cumulative gain; None when nothing has a grade above drop."""
    ideal = sum(
        grade * _discount(p)
        for p, grade in enumerate(sorted((i.grade for i in items), reverse=True), start=1)
    )
    if ideal == 0:
        return None
    return _expected(items, _discount, float) / ideal


def precision_at(items: Sequence[Item], k: int) -> float | None:
    """Share of the top k that is relevant.

    None when k takes the whole set: every ranking then holds the same rows, so
    the number is identical by construction and would read as a tie.
    """
    if len(items) <= k:
        return None
    return _expected(items, lambda p: 1.0 / k if p <= k else 0.0, _relevant)


def recall_at(items: Sequence[Item], k: int) -> float | None:
    """Share of the relevant postings that the top k holds; None when it cannot discriminate."""
    relevant = sum(_relevant(i.grade) for i in items)
    if len(items) <= k or relevant == 0:
        return None
    return _expected(items, lambda p: 1.0 if p <= k else 0.0, _relevant) / relevant


def bootstrap(
    rankings: Mapping[str, Sequence[Item]],
    baseline: str,
    *,
    iterations: int = 1000,
    seed: int = 0,
) -> dict[str, Interval]:
    """nDCG of each ranking minus the baseline's, resampling postings with replacement.

    Paired: every ranking is scored on the same resample, so the interval is on
    the difference and not inflated by which postings a draw happened to pick.
    Seeded, so the same labels and scores give the same interval.

    Each ranking's items must be in the same posting order. A ranking with no
    usable draw is left out rather than reported as zero.
    """
    names = [name for name in rankings if name != baseline]
    size = len(rankings[baseline])
    if size == 0 or any(len(rankings[name]) != size for name in names):
        raise ValueError("every ranking must cover the same postings")

    rng = random.Random(seed)
    deltas: dict[str, list[float]] = {name: [] for name in names}
    for _ in range(iterations):
        draw = [rng.randrange(size) for _ in range(size)]
        base = ndcg([rankings[baseline][i] for i in draw])
        if base is None:
            continue
        for name in names:
            score = ndcg([rankings[name][i] for i in draw])
            if score is not None:
                deltas[name].append(score - base)

    out: dict[str, Interval] = {}
    for name, values in deltas.items():
        if not values:
            continue
        values.sort()
        out[name] = Interval(
            delta=sum(values) / len(values),
            low=values[int(0.025 * (len(values) - 1))],
            high=values[int(0.975 * (len(values) - 1))],
        )
    return out
