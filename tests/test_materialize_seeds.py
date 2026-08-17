"""The seeds are private config now, so how they are resolved is behaviour."""

from __future__ import annotations

from pathlib import Path

import pytest

from ingest import materialize_seeds as ms
from shared.config import Settings


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the module at throwaway dirs so a test never writes real seeds."""
    seed_dir = tmp_path / "seeds"
    example_dir = tmp_path / "examples"
    seed_dir.mkdir()
    example_dir.mkdir()
    monkeypatch.setattr(ms, "SEED_DIR", seed_dir)
    monkeypatch.setattr(ms, "EXAMPLE_DIR", example_dir)


def _write_example(seed: str, text: str) -> None:
    (ms.EXAMPLE_DIR / f"{seed}.example.csv").write_text(text)


def test_env_var_name_matches_the_companies_convention() -> None:
    assert ms.env_var_for("desired_tech") == "DESIRED_TECH_CSV_CONTENT"
    assert ms.env_var_for("allowed_locations") == "ALLOWED_LOCATIONS_CSV_CONTENT"


def test_environment_wins_over_everything() -> None:
    _write_example("desired_tech", "tech,note\nExample,from the example\n")
    (ms.SEED_DIR / "desired_tech.csv").write_text("tech,note\nLocal,from the local file\n")

    origin = ms.materialize_seed(
        "desired_tech", env={"DESIRED_TECH_CSV_CONTENT": "tech,note\ndbt,from the variable"}
    )

    assert origin == "environment"
    assert "from the variable" in (ms.SEED_DIR / "desired_tech.csv").read_text()


def test_private_file_is_used_and_left_untouched() -> None:
    """CI writes the file; every later `make` target must not clobber it."""
    _write_example("desired_tech", "tech,note\nExample,from the example\n")
    target = ms.SEED_DIR / "desired_tech.csv"
    target.write_text("tech,note\nLocal,from the local file\n")

    assert ms.materialize_seed("desired_tech", env={}) == "private file"
    assert "from the local file" in target.read_text()


def test_falls_back_to_the_example_so_a_fork_still_builds() -> None:
    _write_example("desired_tech", "tech,note\nExample,from the example\n")

    assert ms.materialize_seed("desired_tech", env={}) == "example"
    assert "from the example" in (ms.SEED_DIR / "desired_tech.csv").read_text()


def test_blank_environment_variable_is_not_a_seed() -> None:
    """An unset Actions variable expands to an empty string, not to nothing."""
    _write_example("desired_tech", "tech,note\nExample,from the example\n")

    assert ms.materialize_seed("desired_tech", env={"DESIRED_TECH_CSV_CONTENT": "  "}) == "example"


def test_wrong_header_fails_here_not_in_dbt() -> None:
    """A renamed column would otherwise surface as a compile error in a model."""
    with pytest.raises(ValueError, match="expected"):
        ms.materialize_seed(
            "desired_tech", env={"DESIRED_TECH_CSV_CONTENT": "technology,note\ndbt,x"}
        )


def test_header_only_seed_is_rejected() -> None:
    """An empty rule set silently changes what the pipeline delivers."""
    with pytest.raises(ValueError, match="no data rows"):
        ms.materialize_seed("desired_tech", env={"DESIRED_TECH_CSV_CONTENT": "tech,note"})


def test_missing_everything_names_all_three_places_it_looked() -> None:
    with pytest.raises(FileNotFoundError, match="DESIRED_TECH_CSV_CONTENT"):
        ms.materialize_seed("desired_tech", env={})


def test_prod_refuses_the_example_fallback() -> None:
    """The one quiet failure: an unset variable would filter gold to the
    example's locations and still exit 0."""
    _write_example("desired_tech", "tech,note\nExample,from the example\n")

    with pytest.raises(RuntimeError, match="gh variable set DESIRED_TECH_CSV_CONTENT"):
        ms.materialize_seed("desired_tech", env={}, allow_example=False)


def test_prod_still_accepts_the_variable_and_the_private_file() -> None:
    assert (
        ms.materialize_seed(
            "desired_tech",
            env={"DESIRED_TECH_CSV_CONTENT": "tech,note\ndbt,x"},
            allow_example=False,
        )
        == "environment"
    )
    assert ms.materialize_seed("desired_tech", env={}, allow_example=False) == "private file"


def test_run_disallows_the_example_on_the_prod_target() -> None:
    for seed, header in ms.SEED_HEADERS.items():
        _write_example(seed, ",".join(header) + "\n" + ",".join("x" for _ in header) + "\n")
    prod = Settings(pipeline_target="prod", gcp_project="p")

    with pytest.raises(RuntimeError, match="Refusing to fall back"):
        ms.run(env={}, settings=prod)

    assert ms.run(env={}, settings=Settings(pipeline_target="dev")) == 0


def test_run_materializes_every_declared_seed() -> None:
    for seed, header in ms.SEED_HEADERS.items():
        _write_example(seed, ",".join(header) + "\n" + ",".join("x" for _ in header) + "\n")

    assert ms.run(env={}) == 0
    for seed in ms.SEED_HEADERS:
        assert (ms.SEED_DIR / f"{seed}.csv").exists()


def test_committed_examples_are_valid() -> None:
    """The examples are the fork/clone fallback, so a broken one breaks CI."""
    for seed, header in ms.SEED_HEADERS.items():
        example = ms.ROOT / "config" / "seeds" / f"{seed}.example.csv"
        assert example.exists(), f"missing {example}"
        ms._validate(seed, example.read_text(), str(example))
        assert example.read_text().splitlines()[0].split(",") == header
