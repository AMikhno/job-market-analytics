"""Fetch order: boards are interleaved by host, not grouped by source.

The rate limiter paces per host, so the pool only overlaps work when the boards
in flight target different hosts. Grouping by source puts every worker behind
one interval and makes the run the sum of its sources instead of the slowest.
"""

from ingest import pipeline
from ingest.sources import SOURCES
from shared.models import Company

_BY_NAME = {s.name: s for s in SOURCES}


def _company(source: str, ref: str) -> Company:
    return Company(company_name=ref, source=source, board_ref=ref, active=True)


def _work(*pairs: tuple[str, str]) -> list[tuple[object, str, Company]]:
    """Work items shaped like _fetch_all builds them; the adapter is unused here."""
    return [(object(), source, _company(source, ref)) for source, ref in pairs]


def _hosts(work: list[tuple[object, str, Company]]) -> list[str]:
    return [_BY_NAME[source].host_for(company.board_ref) for _, source, company in work]


def test_shared_host_sources_report_one_host_per_ats() -> None:
    """Every board of a single-endpoint ATS shares a host, so it shares a limit."""
    greenhouse = _BY_NAME["greenhouse"]
    assert greenhouse.host_for("alpha") == greenhouse.host_for("beta")
    assert "greenhouse" in greenhouse.host_for("alpha")


def test_subdomain_sources_report_a_host_per_board() -> None:
    """An ATS that gives each company a subdomain pays the interval once each."""
    bamboo = _BY_NAME["bamboohr"]
    assert bamboo.host_for("alpha") != bamboo.host_for("beta")
    assert bamboo.host_for("alpha").startswith("alpha.")


def test_host_for_ignores_non_host_placeholders() -> None:
    """Paging and id placeholders live in the path and must not leak into the host."""
    host = _BY_NAME["smartrecruiters"].host_for("alpha")
    assert "{" not in host
    assert "/" not in host


def test_hosts_overlap_instead_of_running_one_after_another() -> None:
    """The regression this exists for: grouped by source, one host owned every
    worker and the sources ran back to back. Interleaved, a second host's work
    must start before the first host's is finished."""
    work = _work(
        ("greenhouse", "gh1"),
        ("greenhouse", "gh2"),
        ("greenhouse", "gh3"),
        ("ashby", "ash1"),
        ("ashby", "ash2"),
        ("lever", "lev1"),
    )
    sequence = _hosts(pipeline._interleave_by_host(work))
    greenhouse = sequence[0]
    assert sequence.index(greenhouse) < len(sequence) - 1
    others_before_greenhouse_ends = set(
        sequence[: len(sequence) - 1 - sequence[::-1].index(greenhouse)]
    )
    assert len(others_before_greenhouse_ends) > 1, f"no overlap: {sequence}"


def test_the_busiest_host_is_not_starved() -> None:
    """Round-robin would give the host with the most boards one slot per cycle,
    delaying the very host that sets the wall time. Its share of the queue must
    track its share of the boards, not its share of the hosts."""
    work = _work(*[("greenhouse", f"gh{i}") for i in range(8)], *[("lever", "lev1")])
    sequence = _hosts(pipeline._interleave_by_host(work))
    greenhouse = _BY_NAME["greenhouse"].host_for("gh0")
    # 8 of 9 boards are greenhouse, so it must hold most of the first slots.
    assert sequence[:4].count(greenhouse) >= 3, f"starved: {sequence}"


def test_interleave_preserves_every_board_exactly_once() -> None:
    """Reordering must not drop or duplicate a board."""
    work = _work(
        ("greenhouse", "gh1"),
        ("greenhouse", "gh2"),
        ("bamboohr", "bam1"),
        ("ashby", "ash1"),
        ("ashby", "ash2"),
        ("ashby", "ash3"),
    )
    ordered = pipeline._interleave_by_host(work)
    assert len(ordered) == len(work)
    assert {(s, c.board_ref) for _, s, c in ordered} == {(s, c.board_ref) for _, s, c in work}


def test_interleave_keeps_each_hosts_own_order() -> None:
    """Spread across hosts, FIFO within one — a host's queue is not shuffled."""
    work = _work(
        ("greenhouse", "gh1"),
        ("greenhouse", "gh2"),
        ("greenhouse", "gh3"),
        ("lever", "lev1"),
    )
    ordered = pipeline._interleave_by_host(work)
    greenhouse_refs = [c.board_ref for _, s, c in ordered if s == "greenhouse"]
    assert greenhouse_refs == ["gh1", "gh2", "gh3"]


def test_single_host_list_is_unchanged() -> None:
    """No order makes one host faster, so leave it alone."""
    work = _work(("greenhouse", "gh1"), ("greenhouse", "gh2"), ("greenhouse", "gh3"))
    assert pipeline._interleave_by_host(work) == work
