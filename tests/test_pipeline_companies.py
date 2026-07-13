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


def test_multi_segment_board_ref_passes_through_untouched(tmp_path: Path, monkeypatch) -> None:
    # Workday-style boards need tenant/instance/site; the loader must not
    # assume single-token slugs.
    csv_file = tmp_path / "companies.csv"
    csv_file.write_text(
        "company_name,source,board_ref,active,tier,notes\nWdCo,greenhouse,wdco/wd5/External,true,2,\n"
    )
    _use(monkeypatch, str(csv_file))

    (c,) = pipeline.load_companies("greenhouse")

    assert c.board_ref == "wdco/wd5/External"
    assert c.tier == 2


def test_malformed_row_fails_loudly_before_fetching(tmp_path: Path, monkeypatch) -> None:
    # A row missing its board_ref is a config error, not something to skip silently.
    bad = tmp_path / "companies.csv"
    bad.write_text("company_name,source,active,tier,notes\nAcme,lever,true,1,\n")
    _use(monkeypatch, str(bad))

    with pytest.raises(ValidationError):
        pipeline.load_companies("lever")
