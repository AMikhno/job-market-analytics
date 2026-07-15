from ingest.pipeline import ADAPTERS
from ingest.sources import SOURCES, GreenhouseSource, LeverSource


def test_registry_has_both_adapters() -> None:
    adapters = {s.adapter for s in SOURCES}
    assert adapters == {"greenhouse", "lever"}


def test_url_templates_take_a_board_ref() -> None:
    assert "boards-api.greenhouse.io" in GreenhouseSource(name="g").url_template
    assert "api.lever.co" in LeverSource(name="l").url_template
    for src in SOURCES:
        assert "{board_ref}" in src.url_template  # single placeholder, adapter-owned


def test_pipeline_adapters_are_wired_from_the_registry() -> None:
    # one adapter per registered source, carrying the registry's template
    assert set(ADAPTERS) == {s.adapter for s in SOURCES}
    for src in SOURCES:
        assert ADAPTERS[src.adapter].url_template == src.url_template
