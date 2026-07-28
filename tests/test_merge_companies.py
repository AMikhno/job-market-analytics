import csv

from ingest.merge_companies import main, merge, read_companies, write_companies
from shared.models import Company


def _c(name: str, source: str = "greenhouse", ref: str = "ref", **kw) -> Company:
    return Company(company_name=name, source=source, board_ref=ref, **kw)


def test_new_companies_are_added() -> None:
    result = merge([_c("Existing")], [_c("Existing"), _c("Brand New", ref="brandnew")])

    assert [c.company_name for c in result.added] == ["Brand New"]
    assert len(result.rows) == 2


def test_a_hand_corrected_board_ref_survives_a_restage() -> None:
    """The reason merge replaced `cp`: discovery re-derives `harness` from the
    company name every run, but the real board is `REDACTED`. A replace would
    silently undo that fix on every refresh."""
    master = [_c("Harness", ref="REDACTED")]
    incoming = [_c("Harness", ref="harness")]

    result = merge(master, incoming)

    assert result.rows[0].board_ref == "REDACTED"  # master wins
    assert result.conflicts == [("Harness", "greenhouse/REDACTED", "greenhouse/harness")]


def test_a_deliberate_inactive_flag_is_not_reactivated() -> None:
    master = [_c("Parked Co", active=False)]

    result = merge(master, [_c("Parked Co", active=True)])

    assert result.rows[0].active is False


def test_blank_fields_are_filled_from_discovery() -> None:
    """How existing rows acquire a website without disturbing anything else."""
    master = [_c("No Site", website="", notes="")]

    result = merge(master, [_c("No Site", website="nosite.com", notes="Greenhouse")])

    assert result.rows[0].website == "nosite.com"
    assert result.rows[0].notes == "Greenhouse"
    assert sorted(f for _, f in result.filled) == ["notes", "website"]


def test_a_populated_field_is_never_overwritten() -> None:
    master = [_c("Noted", notes="hand-written: contact via referral")]

    result = merge(master, [_c("Noted", notes="auto-detected 2026-07-28")])

    assert result.rows[0].notes == "hand-written: contact via referral"
    assert result.filled == []


def test_an_ats_move_is_reported_not_applied() -> None:
    """A company moving ATS is real information, but applying it automatically
    would be wrong whenever discovery is the one that's mistaken."""
    result = merge([_c("Mover", "greenhouse", "mover")], [_c("Mover", "ashby", "mover")])

    assert result.rows[0].source == "greenhouse"
    assert result.conflicts == [("Mover", "greenhouse/mover", "ashby/mover")]


def test_name_matching_ignores_case_and_spacing() -> None:
    result = merge([_c("Modern  Campus")], [_c("modern campus", ref="other")])

    assert len(result.rows) == 1  # matched, not duplicated
    assert result.added == []


def test_distinct_companies_sharing_a_board_stay_separate() -> None:
    """REDACTED and REDACTED QNX are deliberately separate rows on one board."""
    master = [_c("REDACTED", "workday", "bb"), _c("REDACTED QNX", "workday", "bb")]

    result = merge(master, [_c("REDACTED", "workday", "bb")])

    assert len(result.rows) == 2


def test_round_trip_through_disk_sorts_active_first(tmp_path) -> None:
    master = tmp_path / "companies.csv"
    write_companies(
        [_c("Inactive Co", "workday", "inact", active=False), _c("Active Co", active=True)], master
    )
    incoming = tmp_path / "incoming.csv"
    write_companies([_c("Fresh Co", "lever", "fresh", active=True, website="fresh.co")], incoming)

    assert main([str(master), str(incoming)]) == 0

    rows = list(csv.DictReader(master.open(newline="")))
    assert [r["company_name"] for r in rows] == ["Active Co", "Fresh Co", "Inactive Co"]
    assert rows[1]["website"] == "fresh.co"
    assert read_companies(master)[0].active is True


def test_main_reports_usage_error_without_two_paths() -> None:
    assert main([]) == 2
