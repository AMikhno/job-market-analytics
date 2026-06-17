from ingest.sources import SOURCES, GreenhouseSource, LeverSource


def test_registry_has_both_adapters() -> None:
    adapters = {s.adapter for s in SOURCES}
    assert adapters == {"greenhouse", "lever"}


def test_url_templates() -> None:
    assert "boards-api.greenhouse.io" in GreenhouseSource(name="g").url_template
    assert "api.lever.co" in LeverSource(name="l").url_template
