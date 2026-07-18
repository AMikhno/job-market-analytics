from pathlib import Path

import pytest
from pydantic import ValidationError

from ingest import pipeline
from shared.models import Company


def _use(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.setenv("COMPANIES_CSV", path)


def test_load_companies_returns_typed_active_rows(monkeypatch) -> None:
    # Use the committed example, not a developer's private config/companies.csv.
    _use(monkeypatch, "config/companies.example.csv")

    companies = pipeline.load_companies("lever")

    assert [c.board_ref for c in companies] == ["lever"]  # demo board, active
    assert all(isinstance(c, Company) for c in companies)
    assert companies[0].company_name == "Lever (demo board)"
    assert companies[0].tier == 1


def test_inactive_rows_and_other_sources_excluded(monkeypatch) -> None:
    _use(monkeypatch, "config/companies.example.csv")
    assert pipeline.load_companies("greenhouse") == []  # placeholder is active=false
    assert pipeline.load_companies("workday") == []  # unsupported ATS stays inventory-only


def test_legacy_company_slug_header_still_parses(tmp_path: Path, monkeypatch) -> None:
    # The private list may still use the pre-board_ref header (e.g. the GitHub
    # Actions variable); it must keep working.
    legacy = tmp_path / "companies.csv"
    legacy.write_text(
        "company_name,source,company_slug,active,tier,notes\nAcme,lever,acme,true,1,\n"
    )
    _use(monkeypatch, str(legacy))

    (c,) = pipeline.load_companies("lever")

    assert c.board_ref == "acme"
    assert c.notes == ""  # blank CSV cell falls back to the default


def test_multi_segment_board_ref_parses_untouched() -> None:
    # Workday-style boards need tenant/instance/site; CSV parsing must not split
    # or mangle a multi-segment board_ref. (Per-source *format* validation is a
    # separate concern, exercised in test_sources.py — a multi-segment ref is
    # rejected by bare-token sources but accepted by the source that owns it.)
    c = Company.model_validate(
        {
            "company_name": "WdCo",
            "source": "workday",
            "board_ref": "wdco/wd5/External",
            "active": "true",
            "tier": "2",
            "notes": "",
        }
    )

    assert c.board_ref == "wdco/wd5/External"
    assert c.tier == 2


def test_invalid_board_ref_fails_loudly_before_fetching(tmp_path: Path, monkeypatch) -> None:
    # A URL pasted where a bare board token belongs would build a broken request
    # and silently 404-skip; catch it at load time instead.
    bad = tmp_path / "companies.csv"
    bad.write_text(
        "company_name,source,board_ref,active,tier,notes\n"
        "Acme,greenhouse,https://boards.greenhouse.io/acme,true,1,\n"
    )
    _use(monkeypatch, str(bad))

    with pytest.raises(ValueError, match="invalid board_ref"):
        pipeline.load_companies("greenhouse")


def test_whitespace_in_csv_cells_is_stripped(tmp_path: Path, monkeypatch) -> None:
    # Hand-maintained lists pick up stray spaces; an unstripped source would
    # silently never match its adapter and the row would vanish without error.
    spaced = tmp_path / "companies.csv"
    spaced.write_text(
        "company_name,source,board_ref,active,tier,notes\nShyftlabs, lever, shyftlabs, true, 1, \n"
    )
    _use(monkeypatch, str(spaced))

    (c,) = pipeline.load_companies("lever")

    assert c.source == "lever"
    assert c.board_ref == "shyftlabs"
    assert c.active is True
    assert c.tier == 1


def test_malformed_row_fails_loudly_before_fetching(tmp_path: Path, monkeypatch) -> None:
    # A row missing its board_ref is a config error, not something to skip silently.
    bad = tmp_path / "companies.csv"
    bad.write_text("company_name,source,active,tier,notes\nAcme,lever,true,1,\n")
    _use(monkeypatch, str(bad))

    with pytest.raises(ValidationError):
        pipeline.load_companies("lever")
