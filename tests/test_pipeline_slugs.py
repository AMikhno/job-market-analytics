from ingest import pipeline


def test_load_slugs_filters_active_lever() -> None:
    slugs = pipeline.load_slugs("lever")
    assert "lever" in slugs  # the seed's active Lever board


def test_inactive_rows_excluded() -> None:
    # the placeholder Greenhouse token is active=false in the seed
    assert pipeline.load_slugs("greenhouse") == []
