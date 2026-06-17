from shared.config import Settings


def test_target_defaults_to_dev() -> None:
    s = Settings(_env_file=None)
    assert s.pipeline_target == "dev"
    assert s.is_prod is False


def test_prod_flag() -> None:
    s = Settings(_env_file=None, pipeline_target="prod")
    assert s.is_prod is True


def test_get_settings_returns_instance() -> None:
    from shared.config import Settings, get_settings

    assert isinstance(get_settings(), Settings)
