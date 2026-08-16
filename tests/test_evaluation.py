"""Relevance labels and the fit-vs-keyword ranking comparison."""

from __future__ import annotations

from pathlib import Path

import pytest

from evaluation import report
from evaluation.labels import Label, load_labels


def _write(tmp_path: Path, body: str) -> str:
    path = tmp_path / "labels.csv"
    path.write_text(body)
    return str(path)


def test_loads_labels(tmp_path: Path) -> None:
    labels = load_labels(_write(tmp_path, "job_key,relevant\na:b:1,yes\na:b:2,no\n"))
    assert [x.job_key for x in labels] == ["a:b:1", "a:b:2"]
    assert [x.relevant for x in labels] == [True, False]


@pytest.mark.parametrize("raw", ["yes", "YES", "y", "true", "1"])
def test_truthy_spellings(tmp_path: Path, raw: str) -> None:
    assert load_labels(_write(tmp_path, f"job_key,relevant\na:b:1,{raw}\n"))[0].relevant


@pytest.mark.parametrize("raw", ["no", "NO", "n", "false", "0"])
def test_falsy_spellings(tmp_path: Path, raw: str) -> None:
    assert not load_labels(_write(tmp_path, f"job_key,relevant\na:b:1,{raw}\n"))[0].relevant


def test_unrecognized_value_is_rejected(tmp_path: Path) -> None:
    """Defaulting an unreadable value to False would relabel a relevant posting
    as irrelevant, which moves every metric in the flattering direction."""
    with pytest.raises(ValueError, match="unrecognized relevant value"):
        load_labels(_write(tmp_path, "job_key,relevant\na:b:1,maybe\n"))


def test_duplicate_job_key_is_rejected(tmp_path: Path) -> None:
    """Two rows for one posting means the file was edited twice with different
    answers; keeping either picks a judgement the labeller did not make."""
    with pytest.raises(ValueError, match="duplicate job_key"):
        load_labels(_write(tmp_path, "job_key,relevant\na:b:1,yes\na:b:1,no\n"))


def test_comments_and_blank_keys_are_skipped(tmp_path: Path) -> None:
    body = "job_key,relevant\n# a note,\na:b:1,yes\n,\n"
    assert [x.job_key for x in load_labels(_write(tmp_path, body))] == ["a:b:1"]


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="contains no labels"):
        load_labels(_write(tmp_path, "job_key,relevant\n"))


def test_missing_file_says_how_to_make_one(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="labels.example.csv"):
        load_labels(str(tmp_path / "absent.csv"))


def test_committed_example_parses() -> None:
    assert load_labels("config/labels.example.csv")


def _rows() -> list[dict[str, object]]:
    return [
        {"job_key": "good_fit", "fit_score": 5, "match_score": 0},
        {"job_key": "keyword_bait", "fit_score": 1, "match_score": 9},
        {"job_key": "unscored", "fit_score": None, "match_score": 5},
    ]


def test_ranking_puts_unscored_last() -> None:
    """Null sorts last under both rankings, so an unscored posting cannot
    occupy a top slot it did not earn."""
    assert report._rank(_rows(), "fit_score")[-1] == "unscored"


def test_ranking_is_deterministic_on_ties() -> None:
    """Ties resolve by job_key; otherwise the metric moves between runs on
    identical data and a real change is indistinguishable from noise."""
    tied = [
        {"job_key": "b", "fit_score": 3, "match_score": 1},
        {"job_key": "a", "fit_score": 3, "match_score": 1},
    ]
    assert report._rank(tied, "fit_score") == ["a", "b"]
    assert report._rank(list(reversed(tied)), "fit_score") == ["a", "b"]


def test_precision_at_k_counts_only_labelled_hits() -> None:
    order = [f"k{i}" for i in range(12)]
    labels = {"k0": True, "k1": True, "k7": True}
    result = report.evaluate(report.Ranking("fit", order), labels)
    assert result.relevant_found_at[5] == 2
    assert result.precision_at[5] == pytest.approx(2 / 5)


def test_a_cutoff_that_takes_everything_scores_none_not_a_tie() -> None:
    """With k >= the candidate count every ordering holds the same rows, so the
    number is identical by construction. Reporting it as a tie would read as
    "the rankings performed the same" when it means "this cutoff cannot tell"."""
    result = report.evaluate(report.Ranking("fit", ["a", "b"]), {"a": True})
    assert result.precision_at[5] is None
    assert result.precision_at[20] is None


def test_verdict_refuses_to_judge_on_degenerate_cutoffs() -> None:
    labels = [Label(job_key="a", relevant=True), Label(job_key="b", relevant=False)]
    by_key = {x.job_key: x.relevant for x in labels}
    results = [
        report.evaluate(report.Ranking("fit_score", ["a", "b"]), by_key),
        report.evaluate(report.Ranking("match_score", ["b", "a"]), by_key),
    ]
    text = report._format(results, labels, covered=2)
    assert "Verdict: none" in text
    assert "Label more postings" in text


def test_the_two_rankings_disagree_on_keyword_bait() -> None:
    """The case V2 exists for: a posting dense in keywords whose work is
    unrelated. fit_score must rank it below the sparse-but-relevant one, and
    match_score must not — otherwise the comparison measures nothing."""
    by_fit = report._rank(_rows(), "fit_score")
    by_match = report._rank(_rows(), "match_score")
    assert by_fit.index("good_fit") < by_fit.index("keyword_bait")
    assert by_match.index("keyword_bait") < by_match.index("good_fit")


def test_report_names_a_verdict() -> None:
    """Enough candidates that P@5 discriminates: fit puts both relevant postings
    in the top 5, the keyword ordering buries them."""
    relevant = ["good_a", "good_b"]
    filler = [f"pad{i}" for i in range(8)]
    labels = [Label(job_key=k, relevant=True) for k in relevant]
    labels += [Label(job_key=k, relevant=False) for k in filler]
    by_key = {x.job_key: x.relevant for x in labels}

    results = [
        report.evaluate(report.Ranking("fit_score", relevant + filler), by_key),
        report.evaluate(report.Ranking("match_score", filler + relevant), by_key),
    ]
    text = report._format(results, labels, covered=10)

    assert "fit_score ranks better" in text
    assert "P@5" in text
