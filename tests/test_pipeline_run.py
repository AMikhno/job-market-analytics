import json
import logging
import re
import threading
from datetime import UTC, datetime

import pytest
import requests
import responses

from ingest import pipeline
from ingest.sources import SOURCES, LeverSource
from shared import storage
from shared.config import get_settings
from shared.http import FetchPolicy
from shared.models import IngestRun, RawPosting
from shared.redact import redact_ref


def _seed_history(counts: list[tuple[int, str]]) -> None:
    """Land prior ops.ingest_runs rows for 'lever' as (rows_fetched, status)."""
    settings = get_settings()
    pipeline.ensure_raw_tables(settings)
    now = datetime.now(UTC)
    storage.land_runs(
        [
            IngestRun(
                run_id=f"seed{i}",
                source="lever",
                company_count=1,
                rows_fetched=rows,
                status=status,
                started_at=now,
                finished_at=now,
            )
            for i, (rows, status) in enumerate(counts)
        ],
        settings=settings,
    )


LEVER_URL = LeverSource(name="lever").url_template
LEVER_EU_URL = LeverSource(name="lever").eu_url_template


def _env(monkeypatch, tmp_path):
    # Pin a controlled company list so tests never read a developer's real
    # (gitignored) config/companies.csv. Distinct filename to avoid clashing
    # with tests that write their own tmp_path/"companies.csv".
    default = tmp_path / "default_companies.csv"
    default.write_text(
        "company_name,source,board_ref,active,tier,notes\nLever demo,lever,lever,true,1,\n"
    )
    monkeypatch.setenv("PIPELINE_TARGET", "dev")
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "j.duckdb"))
    monkeypatch.setenv("SUMMARY_PATH", str(tmp_path / "summary.json"))
    monkeypatch.setenv("COMPANIES_CSV", str(default))
    # These tests assert on `responses`' registered-call bookkeeping, which is
    # not thread-safe; concurrency itself is covered below with a fake adapter.
    monkeypatch.setenv("FETCH_WORKERS", "1")
    monkeypatch.setenv("FETCH_MIN_INTERVAL_S", "0")


@responses.activate
def test_run_happy_path_lands_rows_and_ops(tmp_path, monkeypatch, lever_payload) -> None:
    _env(monkeypatch, tmp_path)
    responses.add(responses.GET, LEVER_URL.format(board_ref="lever"), json=lever_payload)

    assert pipeline.run() == 0

    import duckdb

    con = duckdb.connect(str(tmp_path / "j.duckdb"))
    try:
        assert con.execute("select count(*) from raw_lever_jobs").fetchone()[0] == 1
        # greenhouse is inactive, but its raw table exists (empty) so dbt can build
        assert con.execute("select count(*) from raw_greenhouse_jobs").fetchone()[0] == 0
        # one ops row was recorded for the lever source
        assert con.execute("select count(*) from ops_ingest_runs").fetchone()[0] == 1
        status = con.execute("select status from ops_ingest_runs").fetchone()[0]
        assert status == "ok"
    finally:
        con.close()


@responses.activate
def test_zero_rows_warns_but_does_not_fail(tmp_path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)
    responses.add(responses.GET, LEVER_URL.format(board_ref="lever"), json=[])  # empty board

    rc = pipeline.run()

    assert rc == 0  # warn-only: never fails on low volume
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["warnings"] == ["lever"]
    assert summary["failures"] == []


@responses.activate
def test_a_registered_source_with_no_boards_is_reported_not_silent(
    tmp_path, monkeypatch, lever_payload, caplog
) -> None:
    """A source the company list never mentions still has a raw table with a
    freshness gate on it. It cannot be low-volume (it never runs), so without
    this it is invisible until that gate errors ~30h later and points at the
    warehouse instead of at the list -- which is exactly how a stale
    COMPANIES_CSV_CONTENT took the pipeline down for four days."""
    _env(monkeypatch, tmp_path)  # the pinned list holds one lever row and nothing else
    responses.add(responses.GET, LEVER_URL.format(board_ref="lever"), json=lever_payload)

    with caplog.at_level(logging.WARNING, logger="ingest"):
        rc = pipeline.run()

    assert rc == 0  # warn-only: shipping an adapter before its companies is legitimate
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert set(summary["unconfigured"]) == {s.adapter for s in SOURCES if s.active} - {"lever"}
    assert "lever" not in summary["unconfigured"]  # it ran, so it is reported as a source
    assert summary["failures"] == []
    assert any(
        "has no active companies" in r.getMessage() and "greenhouse" in r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING
    )


@responses.activate
def test_a_fully_configured_run_reports_nothing_unconfigured(
    tmp_path, monkeypatch, lever_payload
) -> None:
    """The clause must stay quiet in the healthy case, or it becomes noise the
    reader learns to skip past."""
    companies = tmp_path / "companies.csv"
    companies.write_text(
        "company_name,source,board_ref,active,tier,notes\n"
        + "".join(f"{s.adapter} demo,{s.adapter},demoboard,true,1,\n" for s in SOURCES if s.active)
    )
    _env(monkeypatch, tmp_path)
    monkeypatch.setenv("COMPANIES_CSV", str(companies))
    # Every board 404s; that is a per-source hard failure, but "unconfigured" is
    # about the list, not the network, and must stay empty either way.
    responses.add(responses.GET, re.compile(r"https?://.*"), status=404)

    pipeline.run()

    summary = json.loads((tmp_path / "summary.json").read_text())
    # Assert the run really reached every source first: a list that failed
    # validation would also leave "unconfigured" empty, for the wrong reason.
    assert {s["source"] for s in summary["sources"]} == {s.adapter for s in SOURCES if s.active}
    assert summary["unconfigured"] == []


@responses.activate
def test_a_sharp_volume_drop_is_warned_not_failed(tmp_path, monkeypatch, lever_payload) -> None:
    """rows_fetched is a *census* of what is on the boards, not a count of new
    postings -- raw is append-only and every run re-lands the whole board. With
    slow rotation that number is stable run to run, so a halving means boards
    stopped answering. The absolute floor only ever fires at zero, so it is
    blind to exactly this case."""
    _env(monkeypatch, tmp_path)
    responses.add(responses.GET, LEVER_URL.format(board_ref="lever"), json=lever_payload)
    _seed_history([(10, "ok")] * 6)  # median 10, above the 5-run minimum

    rc = pipeline.run()  # this run lands 1 row -- well under half of 10

    assert rc == 0  # warn-only, like every other volume signal
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["volume_drops"] == [{"source": "lever", "rows": 1, "baseline": 10}]
    assert summary["failures"] == []


@responses.activate
def test_no_drop_warning_without_enough_history(tmp_path, monkeypatch, lever_payload) -> None:
    """A source must not warn on its first runs merely for lacking a baseline."""
    _env(monkeypatch, tmp_path)
    responses.add(responses.GET, LEVER_URL.format(board_ref="lever"), json=lever_payload)
    _seed_history([(10, "ok")] * 2)  # below volume_min_history

    pipeline.run()

    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["volume_drops"] == []


@responses.activate
def test_failed_runs_stay_out_of_the_baseline(tmp_path, monkeypatch, lever_payload) -> None:
    """A failed run lands zero rows. Letting those into the median would drag it
    down and mask the next real drop."""
    _env(monkeypatch, tmp_path)
    responses.add(responses.GET, LEVER_URL.format(board_ref="lever"), json=lever_payload)
    _seed_history([(10, "ok")] * 5 + [(0, "error")] * 9)  # zeros would dominate

    pipeline.run()

    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["volume_drops"] == [{"source": "lever", "rows": 1, "baseline": 10}]


@responses.activate
def test_a_steady_source_does_not_warn(tmp_path, monkeypatch, lever_payload) -> None:
    """The healthy case has to stay silent, or the warning becomes noise."""
    _env(monkeypatch, tmp_path)
    responses.add(responses.GET, LEVER_URL.format(board_ref="lever"), json=lever_payload)
    _seed_history([(1, "ok")] * 6)  # this run also lands 1

    pipeline.run()

    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["volume_drops"] == []


@responses.activate
def test_error_source_hard_fails(tmp_path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)
    responses.add(responses.GET, LEVER_URL.format(board_ref="lever"), status=404)

    rc = pipeline.run()

    assert rc == 1  # a genuine error surfaces as a non-zero exit
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["failures"] == ["lever"]


@responses.activate
def test_one_bad_board_skips_but_keeps_the_good_one(tmp_path, monkeypatch, lever_payload) -> None:
    companies = tmp_path / "companies.csv"
    companies.write_text(
        "company_name,source,board_ref,active,tier,notes\n"
        "GoodCo,lever,goodco,true,1,\n"
        "BadCo,lever,badco,true,1,\n"
    )
    _env(monkeypatch, tmp_path)
    monkeypatch.setenv("COMPANIES_CSV", str(companies))
    responses.add(responses.GET, LEVER_URL.format(board_ref="goodco"), json=lever_payload)
    responses.add(responses.GET, LEVER_URL.format(board_ref="badco"), status=404)
    responses.add(responses.GET, LEVER_EU_URL.format(board_ref="badco"), status=404)

    rc = pipeline.run()

    assert rc == 0  # a 404 on one company doesn't fail the run
    import duckdb

    con = duckdb.connect(str(tmp_path / "j.duckdb"))
    try:
        assert con.execute("select count(*) from raw_lever_jobs").fetchone()[0] == 1
    finally:
        con.close()
    summary = json.loads((tmp_path / "summary.json").read_text())
    lever = next(s for s in summary["sources"] if s["source"] == "lever")
    assert "badco" in (lever["error"] or "")


@responses.activate
def test_skipped_boards_are_reported_and_redacted(tmp_path, monkeypatch, lever_payload) -> None:
    """A board that 404s leaves its source "ok", so before this it reached no
    warning list and the digest could say "all sources healthy" while a company
    silently dropped out. It must now be reported -- and only ever as a redacted
    digest, because the run summary feeds a public CI log."""
    companies = tmp_path / "companies.csv"
    companies.write_text(
        "company_name,source,board_ref,active,tier,notes\n"
        "GoodCo,lever,goodco,true,1,\n"
        "BadCo,lever,badco,true,1,\n"
    )
    _env(monkeypatch, tmp_path)
    monkeypatch.setenv("COMPANIES_CSV", str(companies))
    responses.add(responses.GET, LEVER_URL.format(board_ref="goodco"), json=lever_payload)
    responses.add(responses.GET, LEVER_URL.format(board_ref="badco"), status=404)
    responses.add(responses.GET, LEVER_EU_URL.format(board_ref="badco"), status=404)

    assert pipeline.run() == 0

    summary = json.loads((tmp_path / "summary.json").read_text())
    lever = next(s for s in summary["sources"] if s["source"] == "lever")
    assert lever["skipped_refs"] == [redact_ref("badco")]
    assert lever["skipped_refs"][0].startswith("redacted:")
    # the raw ref must never reach the field that gets printed publicly
    assert "badco" not in " ".join(lever["skipped_refs"])
    # the good board is unaffected
    assert lever["rows"] == 1


def test_missing_company_files_is_caught_and_logged(tmp_path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)
    monkeypatch.setenv("COMPANIES_CSV", str(tmp_path / "nope.csv"))
    monkeypatch.setattr(pipeline, "EXAMPLE_COMPANIES", tmp_path / "also-nope.csv")

    rc = pipeline.run()

    assert rc == 1  # caught at the top, not an uncaught traceback
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["failures"] == ["__pipeline__"]


def test_catastrophic_error_returns_one_and_writes_summary(tmp_path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)

    def boom(_settings, _sources):
        raise RuntimeError("warehouse unreachable")

    monkeypatch.setattr(pipeline.storage, "ensure_raw_tables", boom)

    rc = pipeline.run()

    assert rc == 1
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["failures"] == ["__pipeline__"]


# --- parallel fetch -----------------------------------------------------------
#
# A fake adapter rather than `responses`: these assert on *when* boards run, and
# the HTTP mock's own bookkeeping is not thread-safe.


class _FakeAdapter:
    """Records the boards it was asked for; optionally blocks on a barrier so a
    test can prove the calls overlap, and fails one nominated board."""

    source = "lever"
    url_template = "https://example.test/{board_ref}"
    policy = FetchPolicy(min_interval_s=0)

    def __init__(self, barrier: threading.Barrier | None = None, fail_ref: str = "") -> None:
        self.barrier = barrier
        self.fail_ref = fail_ref
        self.seen: list[str] = []
        self._lock = threading.Lock()

    def fetch(self, session: requests.Session, board_ref: str) -> list[RawPosting]:
        with self._lock:
            self.seen.append(board_ref)
        if self.barrier is not None:
            self.barrier.wait(timeout=5)  # raises BrokenBarrierError if run serially
        if board_ref == self.fail_ref:
            raise requests.HTTPError("404 board not found")
        return [
            RawPosting(
                source="lever",
                company=board_ref,
                external_id=f"{board_ref}-1",
                title="Analytics Engineer",
                url=f"https://example.test/{board_ref}/1",
                description_html="<p>dbt</p>",
                raw={"id": 1},
            )
        ]


def _fake_source(fake: _FakeAdapter) -> LeverSource:
    """A real registry entry (so board_ref validation still applies) that hands
    the pipeline a prebuilt fake adapter instead of a live one."""
    source = LeverSource(name="lever")
    object.__setattr__(source, "build", lambda limiter=None: fake)
    return source


def _four_lever_boards(tmp_path, monkeypatch) -> None:
    companies = tmp_path / "companies.csv"
    companies.write_text(
        "company_name,source,board_ref,active,tier,notes\n"
        + "".join(f"Co{i},lever,board{i},true,1,\n" for i in range(4))
    )
    _env(monkeypatch, tmp_path)
    monkeypatch.setenv("COMPANIES_CSV", str(companies))


def test_boards_are_fetched_concurrently(tmp_path, monkeypatch) -> None:
    """Four boards, four workers, and a barrier all four must reach: if the
    pipeline fetched them one at a time the barrier would never trip and the
    fetches would fail. This is the whole point of the change."""
    _four_lever_boards(tmp_path, monkeypatch)
    monkeypatch.setenv("FETCH_WORKERS", "4")
    fake = _FakeAdapter(barrier=threading.Barrier(4))
    monkeypatch.setitem(pipeline._SOURCE_BY_ADAPTER, "lever", _fake_source(fake))

    assert pipeline.run() == 0

    assert sorted(fake.seen) == ["board0", "board1", "board2", "board3"]
    summary = json.loads((tmp_path / "summary.json").read_text())
    lever = next(s for s in summary["sources"] if s["source"] == "lever")
    assert lever["rows"] == 4 and lever["status"] == "ok"


def test_a_failing_board_stays_isolated_when_run_in_parallel(tmp_path, monkeypatch) -> None:
    """Per-company failure isolation must survive the executor: a worker's
    exception belongs to its own board, not to whichever future is read next."""
    _four_lever_boards(tmp_path, monkeypatch)
    monkeypatch.setenv("FETCH_WORKERS", "4")
    fake = _FakeAdapter(fail_ref="board2")
    monkeypatch.setitem(pipeline._SOURCE_BY_ADAPTER, "lever", _fake_source(fake))

    assert pipeline.run() == 0  # one bad board never fails the run

    summary = json.loads((tmp_path / "summary.json").read_text())
    lever = next(s for s in summary["sources"] if s["source"] == "lever")
    assert lever["rows"] == 3  # the other three landed
    assert lever["skipped_refs"] == [redact_ref("board2")]
    assert "board2" in (lever["error"] or "")


def test_worker_count_never_exceeds_the_work(tmp_path, monkeypatch) -> None:
    """One company, eight configured workers: the pool is sized to the work so a
    small run doesn't spin up threads it has nothing to give."""
    _env(monkeypatch, tmp_path)
    monkeypatch.setenv("FETCH_WORKERS", "8")
    fake = _FakeAdapter()
    monkeypatch.setitem(pipeline._SOURCE_BY_ADAPTER, "lever", _fake_source(fake))
    sizes: list[int] = []
    real_pool = pipeline.ThreadPoolExecutor

    def spy(max_workers=None, **kwargs):  # noqa: ANN001, ANN003 - test double
        sizes.append(max_workers)
        return real_pool(max_workers=max_workers, **kwargs)

    monkeypatch.setattr(pipeline, "ThreadPoolExecutor", spy)

    assert pipeline.run() == 0
    assert sizes == [1]


@pytest.mark.parametrize("workers", ["0", "-4"])
def test_a_nonsense_worker_count_still_runs_one_board_at_a_time(
    tmp_path, monkeypatch, workers: str
) -> None:
    _env(monkeypatch, tmp_path)
    monkeypatch.setenv("FETCH_WORKERS", workers)
    fake = _FakeAdapter()
    monkeypatch.setitem(pipeline._SOURCE_BY_ADAPTER, "lever", _fake_source(fake))

    assert pipeline.run() == 0  # ThreadPoolExecutor(0) would raise
    assert fake.seen == ["lever"]
