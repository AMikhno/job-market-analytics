import pytest

from ingest.adapters.lever import LeverAdapter
from ingest.pipeline import ADAPTERS
from ingest.sources import SOURCES, AshbySource, GreenhouseSource, LeverSource


def test_registry_has_all_adapters() -> None:
    adapters = {s.adapter for s in SOURCES}
    assert adapters == {"greenhouse", "lever", "ashby"}


def test_url_templates_take_a_board_ref() -> None:
    assert "boards-api.greenhouse.io" in GreenhouseSource(name="g").url_template
    assert "api.lever.co" in LeverSource(name="l").url_template
    assert "api.ashbyhq.com" in AshbySource(name="a").url_template
    for src in SOURCES:
        assert "{board_ref}" in src.url_template  # single placeholder, adapter-owned


def test_pipeline_adapters_are_wired_from_the_registry() -> None:
    # one adapter per registered source, carrying the registry's template
    assert set(ADAPTERS) == {s.adapter for s in SOURCES}
    for src in SOURCES:
        assert ADAPTERS[src.adapter].url_template == src.url_template


def test_validate_board_ref_accepts_bare_tokens() -> None:
    # Every registered source takes a bare token (hyphens/dots/underscores ok).
    for src in (GreenhouseSource(name="g"), LeverSource(name="l"), AshbySource(name="a")):
        src.validate_board_ref("example-co_1.io")  # must not raise


@pytest.mark.parametrize(
    "bad",
    ["", "has space", "a/b/c", "https://boards.greenhouse.io/acme", "/leading", "trailing/"],
)
def test_validate_board_ref_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError, match="invalid board_ref"):
        GreenhouseSource(name="g").validate_board_ref(bad)


def test_ashby_board_ref_accepts_inner_spaces() -> None:
    """Ashby board names are display names: "REDACTED" is the real ref
    (verified live -- every de-spaced variant 404s). The shared bare-token rule
    rejected it, and because load_companies validates *before* fetching, such a
    row would hard-fail the whole run rather than warn-and-skip one board."""
    AshbySource(name="a").validate_board_ref("REDACTED")  # must not raise


@pytest.mark.parametrize("bad", ["", " leading", "trailing ", "two  spaces", "a/b", "x y/z"])
def test_ashby_board_ref_still_rejects_malformed(bad: str) -> None:
    # Spaces are allowed *between* tokens only -- not at the edges, not doubled,
    # and slashes/URLs stay rejected exactly as before.
    with pytest.raises(ValueError, match="invalid board_ref"):
        AshbySource(name="a").validate_board_ref(bad)


@pytest.mark.parametrize("spaced", ["REDACTED", "a b"])
def test_non_ashby_sources_still_reject_spaces(spaced: str) -> None:
    for src in (GreenhouseSource(name="g"), LeverSource(name="l")):
        with pytest.raises(ValueError, match="invalid board_ref"):
            src.validate_board_ref(spaced)


def test_lever_source_carries_an_eu_shard_template() -> None:
    src = LeverSource(name="lever")
    assert "api.eu.lever.co" in src.eu_url_template
    assert "{board_ref}" in src.eu_url_template
    adapter = ADAPTERS["lever"]
    assert isinstance(adapter, LeverAdapter)
    assert adapter.eu_url_template == src.eu_url_template  # wired through the registry
