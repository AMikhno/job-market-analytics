import csv
import io

from ingest import export_companies

MASTER = (
    "company_name,source,board_ref,active,tier,website,notes\n"
    "ActiveCo,greenhouse,activeco,true,1,activeco.com,Greenhouse\n"
    "InventoryCo,workday,inventoryco,false,2,inventoryco.com,Workday; no adapter\n"
    "SpacedCo,ashby,Spaced Co,true,1,spaced.co,Ashby\n"
)


def _list(tmp_path, monkeypatch, body: str = MASTER):
    path = tmp_path / "companies.csv"
    path.write_text(body)
    monkeypatch.setenv("COMPANIES_CSV", str(path))
    return path


def test_projection_keeps_only_active_rows(tmp_path, monkeypatch) -> None:
    """The pipeline reads active rows only, so inventory rows have no business
    in the Actions variable -- they just publish the prospect list and eat into
    GitHub's 48 KB per-variable ceiling."""
    _list(tmp_path, monkeypatch)

    companies = export_companies.active_companies()

    assert [c.company_name for c in companies] == ["ActiveCo", "SpacedCo"]


def test_projection_round_trips_through_the_company_model(tmp_path, monkeypatch) -> None:
    """It is a row filter, not a reshape: the output must parse back with the
    same schema so `make validate-companies` checks it exactly like the master."""
    _list(tmp_path, monkeypatch)
    buf = io.StringIO()

    written = export_companies.write_projection(export_companies.active_companies(), buf)

    buf.seek(0)
    rows = [export_companies.Company.model_validate(r) for r in csv.DictReader(buf)]
    assert written == 2
    assert [r.board_ref for r in rows] == ["activeco", "Spaced Co"]  # spaces survive
    assert all(r.active for r in rows)
    assert rows[0].website == "activeco.com"  # website carried, not dropped


def test_projection_preserves_websites_for_recovery(tmp_path, monkeypatch) -> None:
    """The website is the recovery key when a company moves ATS. Keeping it in
    the projection also makes the variable a usable backup of the active list."""
    _list(tmp_path, monkeypatch)
    buf = io.StringIO()

    export_companies.write_projection(export_companies.active_companies(), buf)

    assert "activeco.com" in buf.getvalue()
    assert "inventoryco.com" not in buf.getvalue()  # inactive row excluded entirely


def test_a_list_without_a_website_column_still_projects(tmp_path, monkeypatch) -> None:
    """Older lists predate the column; they must keep working, with website blank."""
    _list(
        tmp_path,
        monkeypatch,
        "company_name,source,board_ref,active,tier,notes\nOldCo,lever,oldco,true,1,legacy\n",
    )
    buf = io.StringIO()

    export_companies.write_projection(export_companies.active_companies(), buf)

    buf.seek(0)
    (row,) = list(csv.DictReader(buf))
    assert row["company_name"] == "OldCo"
    assert row["website"] == ""
