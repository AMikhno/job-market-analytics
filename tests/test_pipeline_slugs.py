from ingest import pipeline


def test_load_slugs_filters_active_lever(monkeypatch) -> None:
    # Use the committed example, not a developer's private config/companies.csv.
    monkeypatch.setenv("COMPANIES_CSV", "config/companies.example.csv")
    assert "lever" in pipeline.load_slugs("lever")  # demo board, active


def test_inactive_rows_excluded(monkeypatch) -> None:
    monkeypatch.setenv("COMPANIES_CSV", "config/companies.example.csv")
    assert pipeline.load_slugs("greenhouse") == []  # placeholder is active=false
