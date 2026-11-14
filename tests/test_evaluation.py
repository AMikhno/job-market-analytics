"""Relevance labels, their resolution onto postings, and the ranking comparison."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from evaluation import metrics, report, template
from evaluation.labels import BEST, DROP, FIT, STRETCH, Label, load_labels
from evaluation.matching import Candidate, normalise_url, resolve
from evaluation.metrics import Item
from shared.config import Settings


def _write(tmp_path: Path, body: str) -> str:
    path = tmp_path / "labels.csv"
    path.write_text(body)
    return str(path)


# --------------------------------------------------------------------- labels


def test_loads_graded_labels_keyed_by_url_or_job_key(tmp_path: Path) -> None:
    body = "url,tier\nhttps://x/1,best fit\nhttps://x/2,fit\na:b:3,stretch\na:b:4,drop\n"
    labels = load_labels(_write(tmp_path, body))
    assert [x.grade for x in labels] == [BEST, FIT, STRETCH, DROP]
    assert [x.is_url for x in labels] == [True, True, False, False]
    # Fit is the line: a digest should surface it, and a stretch it need not.
    assert [x.relevant for x in labels] == [True, True, False, False]


@pytest.mark.parametrize(
    ("raw", "grade"),
    [
        ("yes", BEST),
        ("Y", BEST),
        ("true", BEST),
        ("1", BEST),
        ("Best Fit", BEST),
        ("best_fit", BEST),
        ("fit", FIT),
    ]
    + [
        ("maybe", STRETCH),
        ("stretch", STRETCH),
        ("no", DROP),
        ("N", DROP),
        ("0", DROP),
        ("drop", DROP),
    ],
)
def test_verdict_spellings(tmp_path: Path, raw: str, grade: int) -> None:
    """The worksheet's yes/no and a hand-kept yes/maybe/no both still load."""
    assert load_labels(_write(tmp_path, f"job_key,relevant\na:b:1,{raw}\n"))[0].grade == grade


def test_unrecognized_value_is_rejected(tmp_path: Path) -> None:
    """Defaulting an unreadable value to drop would relabel a relevant posting
    as irrelevant, which moves every metric in the flattering direction."""
    with pytest.raises(ValueError, match="unrecognized verdict"):
        load_labels(_write(tmp_path, "job_key,relevant\na:b:1,perhaps\n"))


def test_duplicate_key_is_rejected(tmp_path: Path) -> None:
    """Two rows for one posting means the file was edited twice with different
    answers; keeping either picks a judgement the labeller did not make."""
    with pytest.raises(ValueError, match="duplicate key"):
        load_labels(_write(tmp_path, "url,tier\nhttps://x/1,best fit\nhttps://X/1,drop\n"))


def test_an_unanswered_row_is_skipped_not_counted_as_drop(tmp_path: Path) -> None:
    """The worksheet arrives pre-filled with rows to judge. Reading a blank as
    a rejection would invent judgements the labeller declined to make."""
    body = "job_key,relevant\na:b:1,yes\na:b:2,\na:b:3,no\n"
    assert [x.key for x in load_labels(_write(tmp_path, body))] == ["a:b:1", "a:b:3"]


def test_extra_worksheet_columns_are_ignored(tmp_path: Path) -> None:
    """The template ships title and company so a row can be judged without
    opening it; the loader must not care. job_key wins over url when both are
    present, because it is the exact identity."""
    body = "job_key,relevant,title,url\na:b:1,yes,Some Role,https://x\n"
    label = load_labels(_write(tmp_path, body))[0]
    assert (label.key, label.grade) == ("a:b:1", BEST)


def test_comments_and_blank_keys_are_skipped(tmp_path: Path) -> None:
    body = "job_key,relevant\n# a note,\na:b:1,yes\n,\n"
    assert [x.key for x in load_labels(_write(tmp_path, body))] == ["a:b:1"]


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="contains no labels"):
        load_labels(_write(tmp_path, "job_key,relevant\n"))


def test_missing_file_says_how_to_make_one(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="labels.example.csv"):
        load_labels(str(tmp_path / "absent.csv"))


def test_committed_example_parses() -> None:
    assert load_labels("config/labels.example.csv")


def test_a_blank_key_is_not_a_label() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        Label(key="  ", grade=BEST)


# ------------------------------------------------------------------- matching


def _candidate(job_key: str, **fields: object) -> Candidate:
    return Candidate.model_validate({"job_key": job_key, **fields})


def test_url_matches_whatever_its_encoding_case_or_trailing_slash() -> None:
    posting = _candidate("k1", url="https://jobs.example.com/Two%20Words/abc/")
    label = Label(key="https://jobs.example.com/two words/abc#apply", grade=BEST)
    assert resolve([label], [posting]).grades == {"k1": BEST}


def test_a_redirected_url_matches_on_the_posting_id_it_carries() -> None:
    """The URL a person copies is where the browser landed, often a
    company-hosted page. The posting id survives the redirect."""
    posting = _candidate(
        "k1", url="https://boards.example.com/acme/jobs/7000001", external_id="7000001"
    )
    label = Label(key="https://careers.acme.example/opportunity/7000001?jid=7000001", grade=FIT)
    assert resolve([label], [posting]).grades == {"k1": FIT}


def test_a_uuid_id_matches_too() -> None:
    uid = "0a1b2c3d-1111-4222-8333-444455556666"
    posting = _candidate("k1", url=f"https://a.example/x/{uid}/apply", external_id=uid)
    label = Label(key=f"https://b.example/y/{uid}", grade=BEST)
    assert resolve([label], [posting]).grades == {"k1": BEST}


def test_no_match_and_an_ambiguous_match_are_both_reported_not_guessed() -> None:
    """Dropping an unmatched label silently shrinks the evaluation set; picking
    one of two candidates attaches a judgement to a posting nobody judged."""
    twins = [
        _candidate("k1", url="https://a.example/1", external_id="555555"),
        _candidate("k2", url="https://b.example/2", external_id="555555"),
    ]
    labels = [
        Label(key="https://c.example/jobs/555555", grade=BEST),
        Label(key="https://c.example/jobs/999999", grade=BEST),
        Label(key="gone:acme:1", grade=DROP),
    ]
    resolution = resolve(labels, twins)
    assert resolution.grades == {}
    assert resolution.unmatched == [x.key for x in labels]


def test_two_labels_on_one_posting_must_agree() -> None:
    posting = _candidate("k1", url="https://a.example/jobs/777777", external_id="777777")
    same = [Label(key="k1", grade=BEST), Label(key="https://a.example/jobs/777777", grade=BEST)]
    assert resolve(same, [posting]).grades == {"k1": BEST}
    with pytest.raises(ValueError, match="disagree"):
        resolve([same[0], Label(key="https://a.example/jobs/777777", grade=DROP)], [posting])


def test_an_alphanumeric_shortcode_id_matches() -> None:
    """Not every ATS numbers its postings; some use a short code in the path."""
    posting = _candidate(
        "k1", url="https://apply.example.com/acme/j/AB12CD34EF", external_id="AB12CD34EF"
    )
    label = Label(key="https://apply.example.com/j/AB12CD34EF/", grade=BEST)
    assert resolve([label], [posting]).grades == {"k1": BEST}


def test_a_short_id_only_matches_when_the_url_names_the_board() -> None:
    """An id like 48 is a per-board counter. Matched alone it would attach the
    judgement to whichever company happened to hold the only 48 in silver."""
    ours = _candidate(
        "k1", company="acme", url="https://acme.example.com/careers/48/detail", external_id="48"
    )
    theirs = _candidate(
        "k2", company="globex", url="https://globex.example.com/x/48/detail", external_id="48"
    )
    label = Label(key="https://acme.example.com/careers/48", grade=FIT)
    assert resolve([label], [ours, theirs]).grades == {"k1": FIT}
    assert resolve([label], [theirs]).unmatched == [label.key]


def test_a_skipped_label_is_neither_graded_nor_assumed_a_rejection(tmp_path: Path) -> None:
    """A posting that closed before it was read has no judgement. Left out of
    the file, a window would count it as drop; graded, it would be invented."""
    labels = load_labels(
        _write(tmp_path, "url,tier\nhttps://x.example/jobs/100002,skip\ngood_fit,best fit\n")
    )
    assert labels[0].grade is None and not labels[0].relevant

    window = (date(2026, 3, 1), date(2026, 3, 3))
    ev = report.evaluate(labels, _postings(), window)
    # good_fit, plus `unscored` assumed drop; keyword_bait is skipped, not counted.
    assert (ev.judged, ev.assumed_drop) == (2, 1)


def test_normalise_url_is_idempotent() -> None:
    once = normalise_url("HTTPS://X.example/A%20B/")
    assert normalise_url(once) == once == "https://x.example/a b"


# -------------------------------------------------------------------- metrics


def _items(*pairs: tuple[int, float]) -> list[Item]:
    return [Item(grade=g, key=(k,)) for g, k in pairs]


def test_untied_ranking_scores_as_the_plain_definition() -> None:
    perfect = _items((BEST, 3), (FIT, 2), (DROP, 1))
    assert metrics.ndcg(perfect) == pytest.approx(1.0)
    worst = _items((BEST, 1), (FIT, 2), (DROP, 3))
    assert metrics.ndcg(worst) is not None and metrics.ndcg(worst) < 1.0


def test_a_tie_is_scored_as_the_average_of_its_orderings() -> None:
    """One relevant posting tied with three irrelevant ones for four places: a
    hash tie-break would report P@2 as 0 or 0.5 depending on the hash. The
    expectation is 1/4 of the group in each place, so 0.25."""
    tied = _items((BEST, 1), (DROP, 1), (DROP, 1), (DROP, 1), (DROP, 0))
    assert metrics.precision_at(tied, 2) == pytest.approx(0.25)
    assert metrics.recall_at(tied, 2) == pytest.approx(0.5)


def test_tied_metric_does_not_depend_on_input_order() -> None:
    tied = _items((BEST, 1), (DROP, 1), (FIT, 1), (DROP, 0))
    assert metrics.ndcg(tied) == pytest.approx(metrics.ndcg(list(reversed(tied))))


def test_a_cutoff_that_takes_everything_scores_none_not_a_tie() -> None:
    """With k >= the candidate count every ordering holds the same rows, so the
    number is identical by construction. Reporting it would read as "the
    rankings performed the same" when it means "this cutoff cannot tell"."""
    two = _items((BEST, 2), (DROP, 1))
    assert metrics.precision_at(two, 5) is None
    assert metrics.recall_at(two, 5) is None


def test_nothing_above_drop_has_no_ndcg_or_recall() -> None:
    drops = _items(*[(DROP, float(i)) for i in range(8)])
    assert metrics.ndcg(drops) is None
    assert metrics.recall_at(drops, 5) is None


def _separated(n: int = 40) -> dict[str, list[Item]]:
    """A baseline that ranks the relevant postings last and a challenger that
    ranks them first, over enough postings that no resample hides it."""
    grades = [BEST if i < 8 else DROP for i in range(n)]
    return {
        "match_score": [Item(g, (float(i),)) for i, g in enumerate(grades)],
        "fit_score": [Item(g, (float(-i),)) for i, g in enumerate(grades)],
    }


def test_bootstrap_calls_a_clear_win_better() -> None:
    interval = metrics.bootstrap(_separated(), "match_score", iterations=200)["fit_score"]
    assert interval.low > 0
    assert interval.verdict == "better"


def test_bootstrap_calls_the_mirror_image_worse() -> None:
    rankings = _separated()
    flipped = {"match_score": rankings["fit_score"], "fit_score": rankings["match_score"]}
    assert metrics.bootstrap(flipped, "match_score", iterations=200)["fit_score"].verdict == "worse"


def test_bootstrap_refuses_a_verdict_on_identical_rankings() -> None:
    """The failure this replaces: any non-zero sum of differences printed
    "ranks better". Identical rankings must come back indistinguishable."""
    same = _separated()["fit_score"]
    interval = metrics.bootstrap(
        {"match_score": same, "fit_score": same}, "match_score", iterations=50
    )
    assert interval["fit_score"].verdict == "not distinguishable"


def test_bootstrap_is_reproducible() -> None:
    a = metrics.bootstrap(_separated(), "match_score", iterations=100)
    assert a == metrics.bootstrap(_separated(), "match_score", iterations=100)


def test_bootstrap_rejects_rankings_over_different_postings() -> None:
    rankings = _separated()
    rankings["fit_score"] = rankings["fit_score"][:-1]
    with pytest.raises(ValueError, match="same postings"):
        metrics.bootstrap(rankings, "match_score")


def test_bootstrap_omits_a_ranking_with_no_usable_draw() -> None:
    drops = _items(*[(DROP, float(i)) for i in range(6)])
    assert metrics.bootstrap({"match_score": drops, "fit_score": drops}, "match_score") == {}


# --------------------------------------------------------------------- report


def _postings() -> list[Candidate]:
    """The case V2 exists for: a posting dense in keywords whose work is
    unrelated, and a sparse posting that is a real fit."""
    day = datetime(2026, 3, 2, 12)
    rows = [
        _candidate(
            "good_fit",
            url="https://x.example/jobs/100001",
            external_id="100001",
            match_score=0,
            fit_score=5,
            first_seen_at=day,
        ),
        _candidate(
            "keyword_bait",
            url="https://x.example/jobs/100002",
            external_id="100002",
            match_score=9,
            fit_score=1,
            first_seen_at=day,
        ),
        _candidate(
            "unscored",
            url="https://x.example/jobs/100003",
            external_id="100003",
            match_score=5,
            first_seen_at=day,
        ),
    ]
    rows += [
        _candidate(f"pad{i}", match_score=3, fit_score=1, first_seen_at=day + timedelta(days=30))
        for i in range(8)
    ]
    return rows


def _labels() -> list[Label]:
    return [
        Label(key="https://x.example/jobs/100001", grade=BEST),
        Label(key="keyword_bait", grade=DROP),
        Label(key="unscored", grade=DROP),
    ] + [Label(key=f"pad{i}", grade=DROP) for i in range(8)]


def test_the_rankings_disagree_on_keyword_bait() -> None:
    ev = report.evaluate(_labels(), _postings())
    by_name = {r.name: r for r in ev.results}
    # fit_score puts the one fit first; the keyword score puts it last.
    assert by_name["fit_score"].ndcg == pytest.approx(1.0)
    assert by_name["match_score"].ndcg is not None and by_name["match_score"].ndcg < 0.5
    assert by_name["fit_score"].recall_at[5] == pytest.approx(1.0)
    assert by_name["match_score"].recall_at[5] == pytest.approx(0.0)


def test_an_unscored_posting_sorts_last_under_fit_score() -> None:
    """A null cannot occupy a top slot it did not earn: relabel the unscored
    posting as the only fit and fit_score must find nothing in its top five."""
    labels = [Label(key=x.key, grade=BEST if x.key == "unscored" else DROP) for x in _labels()]
    labels[0] = Label(key="good_fit", grade=DROP)
    fit = next(r for r in report.evaluate(labels, _postings()).results if r.name == "fit_score")
    assert fit.recall_at[5] == pytest.approx(0.0)


def test_a_ranking_nothing_is_scored_under_is_reported_as_not_running() -> None:
    """Embeddings off means similarity is null everywhere, and its order is the
    baseline's. Grading that would compare the baseline with itself."""
    ev = report.evaluate(_labels(), _postings())
    assert next(r for r in ev.results if r.name == "similarity").scored == 0
    assert "similarity" not in ev.intervals
    assert "not running" in report.format_report(ev)


def test_similarity_is_graded_once_it_exists() -> None:
    postings = [
        c.model_copy(update={"similarity": 0.9 if c.job_key == "good_fit" else 0.1})
        for c in _postings()
    ]
    ev = report.evaluate(_labels(), postings)
    assert next(r for r in ev.results if r.name == "similarity").ndcg == pytest.approx(1.0)
    assert "similarity" in ev.intervals


def test_unmatched_labels_are_counted_and_listed() -> None:
    labels = [*_labels(), Label(key="https://x.example/jobs/424242", grade=BEST)]
    ev = report.evaluate(labels, _postings())
    assert ev.unmatched == ["https://x.example/jobs/424242"]
    text = report.format_report(ev)
    assert "Unmatched:     1" in text
    assert "424242" in text


def test_window_counts_unlabelled_postings_as_drop() -> None:
    """Without the window an unlabelled posting is simply absent; with it, a
    posting first seen in a span that was read in full is a known rejection —
    which is what lets recall be measured against everything that arrived."""
    kept = [Label(key="good_fit", grade=BEST)]
    without = report.evaluate(kept, _postings())
    assert (without.judged, without.assumed_drop) == (1, 0)

    window = (date(2026, 3, 1), date(2026, 3, 3))
    within = report.evaluate(kept, _postings(), window)
    # The two other postings first seen that day, not the pads a month later.
    assert (within.judged, within.assumed_drop) == (3, 2)
    assert "counted as drop" in report.format_report(within)


def test_crosstab_shows_where_fit_score_put_each_grade() -> None:
    ev = report.evaluate(_labels(), _postings())
    assert ev.crosstab[(BEST, 5)] == 1
    assert ev.crosstab[(DROP, 1)] == 9
    assert ev.crosstab[(DROP, None)] == 1
    assert "fit_score by grade" in report.format_report(ev)


def test_long_unmatched_list_is_truncated_in_the_report() -> None:
    labels = [Label(key=f"gone:{i}", grade=DROP) for i in range(14)] + _labels()
    assert "… and 4 more" in report.format_report(report.evaluate(labels, _postings()))


def _with_tiers(tiers: dict[str, int]) -> list[Candidate]:
    return [c.model_copy(update={"tier_rank": tiers.get(c.job_key)}) for c in _postings()]


def test_the_tier_is_graded_as_a_ranking_once_postings_are_assessed() -> None:
    off = report.evaluate(_labels(), _postings())
    assert next(r for r in off.results if r.name == "fit_tier").scored == 0
    assert "fit_tier" not in off.intervals

    on = report.evaluate(_labels(), _with_tiers({"good_fit": 3, "keyword_bait": 0}))
    tier = next(r for r in on.results if r.name == "fit_tier")
    assert tier.scored == 2
    assert tier.ndcg == pytest.approx(1.0)
    assert "fit_tier" in on.intervals


def test_tier_is_compared_with_the_grade_it_claims() -> None:
    """The tier uses the labels' own grades, so disagreement is countable. The
    count that matters most is a posting worth applying to that the tier would
    collapse out of the digest."""
    labels = [
        Label(key="good_fit", grade=BEST),
        Label(key="keyword_bait", grade=STRETCH),
        Label(key="unscored", grade=DROP),
    ]
    ev = report.evaluate(labels, _with_tiers({"good_fit": 3, "keyword_bait": 0, "unscored": 0}))
    assert ev.tier_table[(BEST, 3)] == 1
    assert ev.tier_table[(STRETCH, 0)] == 1
    text = report.format_report(ev)
    assert "Tier agrees with the grade on 2 of 3 assessed (67%)." in text
    assert "tiered drop (would be collapsed): 1." in text


def test_no_agreement_line_when_nothing_is_assessed() -> None:
    assert "Tier agrees" not in report.format_report(report.evaluate(_labels(), _postings()))


def _settings_with_silver(tmp_path: Path, scored: bool = True) -> Settings:
    settings = Settings(_env_file=None, duckdb_path=str(tmp_path / "jobs.duckdb"))
    con = duckdb.connect(settings.duckdb_path)
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS main_silver")
        con.execute(
            """CREATE TABLE main_silver.silver_jobs (
                job_key VARCHAR, content_hash VARCHAR, url VARCHAR, external_id VARCHAR,
                first_seen_at TIMESTAMP, match_score BIGINT, company VARCHAR)"""
        )
        con.execute(
            "CREATE TABLE main_silver.int_jobs_scored (content_hash VARCHAR, fit_score BIGINT)"
        )
        con.execute(
            "CREATE TABLE main_silver.int_jobs_matched (content_hash VARCHAR, similarity DOUBLE)"
        )
        con.execute(
            "CREATE TABLE main_silver.int_jobs_tiered (content_hash VARCHAR, tier_rank BIGINT)"
        )
        for i in range(8):
            con.execute(
                "INSERT INTO main_silver.silver_jobs VALUES (?,?,?,?,?,?,?)",
                [
                    f"k{i}",
                    f"h{i}",
                    f"https://x.example/jobs/{500000 + i}",
                    str(500000 + i),
                    datetime(2026, 3, 2),
                    i,
                    "acme",
                ],
            )
            if scored:
                # 9 is out of range: nulled, exactly as gold does.
                con.execute(
                    "INSERT INTO main_silver.int_jobs_scored VALUES (?,?)",
                    [f"h{i}", 9 if i == 7 else 5 - i % 5],
                )
    finally:
        con.close()
    return settings


def test_run_reads_silver_so_a_closed_posting_is_still_graded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """This warehouse has no gold table at all. Gold holds live postings only,
    so a label read against it loses rows every day; silver keeps them."""
    settings = _settings_with_silver(tmp_path)
    labels = _write(
        tmp_path, "url,tier\nhttps://x.example/jobs/500001,best fit\nk0,drop\nk7,drop\n"
    )

    assert report.run(settings, labels) == 0

    out = capsys.readouterr().out
    assert "Matched:       3" in out
    assert "best fit 1" in out
    candidates = {c.job_key: c for c in report.fetch_candidates(settings)}
    assert candidates["k1"].fit_score == 4
    assert candidates["k7"].fit_score is None


def test_run_fails_when_no_label_resolves(tmp_path: Path) -> None:
    settings = _settings_with_silver(tmp_path)
    assert report.run(settings, _write(tmp_path, "job_key,relevant\nlong:gone:1,yes\n")) == 1


def test_window_argument_parses_two_iso_dates() -> None:
    args = report._parse_args(["--window", "2026-03-01", "2026-03-31"])
    assert args.window == [date(2026, 3, 1), date(2026, 3, 31)]
    assert report._parse_args([]).window is None


# ------------------------------------------------------------------- template


def _settings_with_gold(tmp_path: Path, rows: list[dict[str, object]]) -> Settings:
    settings = Settings(_env_file=None, duckdb_path=str(tmp_path / "jobs.duckdb"))
    con = duckdb.connect(settings.duckdb_path)
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS main_gold")
        con.execute(
            """CREATE TABLE main_gold.fct_job_postings (
                job_key VARCHAR, title VARCHAR, company VARCHAR, location VARCHAR,
                geo_restriction VARCHAR, fit_score BIGINT, match_score BIGINT,
                url VARCHAR, first_seen_at TIMESTAMP)"""
        )
        for i, r in enumerate(rows):
            con.execute(
                "INSERT INTO main_gold.fct_job_postings VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    r.get("job_key", f"k{i}"),
                    r.get("title", "Analytics Engineer"),
                    "acme",
                    "Ottawa",
                    r.get("geo_restriction"),
                    r.get("fit_score"),
                    r.get("match_score", 0),
                    "https://x",
                    datetime(2026, 8, 1) + timedelta(minutes=i),
                ],
            )
    finally:
        con.close()
    return settings


def test_template_writes_a_worksheet(tmp_path: Path) -> None:
    settings = _settings_with_gold(tmp_path, [{"fit_score": 5}, {"fit_score": 1}])
    out = tmp_path / "labels.csv"

    assert template.run(settings, str(out)) == 0

    text = out.read_text()
    assert "job_key,relevant,title" in text
    # The verdict column ships empty — the whole point is that a person fills it.
    assert ",," in text


def test_template_samples_across_the_score_range(tmp_path: Path) -> None:
    """A worksheet drawn only from the top can measure precision but never
    recall: if the scorer buried something relevant at rank 900, a top-N sample
    never contains it and the evaluation reports that all is well."""
    rows: list[dict[str, object]] = [{"fit_score": s} for s in (5, 5, 4, 3, 2, 1, 1)]
    rows.append({"fit_score": None})
    settings = _settings_with_gold(tmp_path, rows)
    out = tmp_path / "labels.csv"

    template.run(settings, str(out), size=12)

    scores = {line.split(",")[6] for line in out.read_text().splitlines()[1:]}
    assert len(scores) >= 4  # several bands represented, not just the top


def test_template_refuses_to_overwrite(tmp_path: Path) -> None:
    """This file is hand-typed judgement. Regenerating it is never worth
    destroying an evening's work."""
    settings = _settings_with_gold(tmp_path, [{"fit_score": 3}])
    out = tmp_path / "labels.csv"
    out.write_text("job_key,relevant\nprecious,yes\n")

    assert template.run(settings, str(out)) == 1
    assert "precious" in out.read_text()


def test_template_reports_empty_gold(tmp_path: Path) -> None:
    settings = _settings_with_gold(tmp_path, [])
    assert template.run(settings, str(tmp_path / "labels.csv")) == 1
