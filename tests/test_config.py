import pytest
from pydantic import ValidationError

from shared.config import Settings


def test_target_defaults_to_dev() -> None:
    s = Settings(_env_file=None)
    assert s.pipeline_target == "dev"
    assert s.is_prod is False


def test_prod_flag() -> None:
    s = Settings(_env_file=None, pipeline_target="prod")
    assert s.is_prod is True


def test_workday_locations_parse_from_json(monkeypatch) -> None:
    monkeypatch.setenv(
        "WORKDAY_LOCATIONS",
        '[{"countries": ["Somewhere"]},'
        ' {"countries": ["Elsewhere", "Elsewhere Proper"], "location_types": ["Remote"]}]',
    )

    rules = Settings(_env_file=None).workday_locations

    assert [r.countries for r in rules] == [["Somewhere"], ["Elsewhere", "Elsewhere Proper"]]
    assert rules[0].location_types == [] and rules[1].location_types == ["Remote"]


@pytest.mark.parametrize("unset", ["", "   "])
def test_an_unset_workday_variable_means_no_rules(monkeypatch, unset: str) -> None:
    """An Actions variable that was never created still arrives, as "": that is
    "no rules" (the adapter then refuses to fetch), not a startup crash."""
    monkeypatch.setenv("WORKDAY_LOCATIONS", unset)
    assert Settings(_env_file=None).workday_locations == []


@pytest.mark.parametrize(
    "bad", ["not json", '[{"location_types": ["Remote"]}]', '[{"countries": []}]']
)
def test_a_malformed_workday_rule_fails_at_startup(monkeypatch, bad: str) -> None:
    monkeypatch.setenv("WORKDAY_LOCATIONS", bad)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_get_settings_returns_instance() -> None:
    from shared.config import Settings, get_settings

    assert isinstance(get_settings(), Settings)
