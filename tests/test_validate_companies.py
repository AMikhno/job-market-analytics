from pathlib import Path

import pytest

from ingest import validate_companies


def _use(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.setenv("COMPANIES_CSV", path)


def test_example_list_is_valid(monkeypatch) -> None:
    _use(monkeypatch, "config/companies.example.csv")
    assert validate_companies.validate_company_list() == []


def test_invalid_board_ref_is_reported(tmp_path: Path, monkeypatch) -> None:
    bad = tmp_path / "companies.csv"
    bad.write_text(
        "company_name,source,board_ref,active,tier,notes\n"
        "Acme,greenhouse,https://boards.greenhouse.io/acme,true,1,\n"
    )
    _use(monkeypatch, str(bad))

    problems = validate_companies.validate_company_list()

    assert len(problems) == 1
    assert "invalid board_ref" in problems[0]


def test_malformed_row_is_reported(tmp_path: Path, monkeypatch) -> None:
    bad = tmp_path / "companies.csv"
    bad.write_text("company_name,source,active,tier,notes\nAcme,lever,true,1,\n")
    _use(monkeypatch, str(bad))

    problems = validate_companies.validate_company_list()

    assert len(problems) == 1
    assert "malformed" in problems[0]


def test_unregistered_ats_row_skips_format_check(tmp_path: Path, monkeypatch) -> None:
    # A Workday row (no adapter yet) parses but has no board_ref rule to apply,
    # so its multi-segment ref must not be flagged.
    lst = tmp_path / "companies.csv"
    lst.write_text(
        "company_name,source,board_ref,active,tier,notes\nWdCo,workday,wdco/wd5/External,false,2,\n"
    )
    _use(monkeypatch, str(lst))

    assert validate_companies.validate_company_list() == []


def test_main_returns_nonzero_on_problems(tmp_path: Path, monkeypatch) -> None:
    bad = tmp_path / "companies.csv"
    bad.write_text("company_name,source,board_ref,active,tier,notes\nAcme,lever,bad ref,true,1,\n")
    _use(monkeypatch, str(bad))

    assert validate_companies.main() == 1


def test_main_returns_zero_on_valid_list(monkeypatch) -> None:
    _use(monkeypatch, "config/companies.example.csv")
    assert validate_companies.main() == 0
