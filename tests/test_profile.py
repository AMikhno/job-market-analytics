"""Profile loading, validation, and prompt rendering (V2 scoring input)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from shared.profile import (
    EXAMPLE_PROFILE,
    PROMPT_VERSION,
    Profile,
    load_profile,
    render_prompt,
)

VALID: dict[str, object] = {
    "target_roles": ["Analytics Engineer", "Data Analyst"],
    "core_skills": ["SQL", "dbt"],
    "nice_to_have_skills": ["Airflow"],
    "seniority": "senior individual contributor",
    "constraints": ["no relocation"],
    "summary": "Builds and maintains analytics pipelines end to end.",
}


def _write(tmp_path: Path, data: object) -> str:
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(data))
    return str(path)


def test_valid_profile_parses() -> None:
    profile = Profile.model_validate(VALID)
    assert profile.target_roles == ["Analytics Engineer", "Data Analyst"]
    assert profile.nice_to_have_skills == ["Airflow"]


def test_optional_lists_default_to_empty() -> None:
    minimal = {k: v for k, v in VALID.items() if k not in ("nice_to_have_skills", "constraints")}
    profile = Profile.model_validate(minimal)
    assert profile.nice_to_have_skills == []
    assert profile.constraints == []


@pytest.mark.parametrize("field", ["target_roles", "core_skills"])
def test_required_lists_reject_empty(field: str) -> None:
    """An empty core list would score every posting against nothing."""
    with pytest.raises(ValidationError):
        Profile.model_validate({**VALID, field: []})


def test_blank_list_entry_is_rejected() -> None:
    """A stray "- " in the YAML is invisible in the file; fail loudly instead
    of rendering it as an empty bullet that reads like a missing requirement."""
    with pytest.raises(ValidationError):
        Profile.model_validate({**VALID, "core_skills": ["SQL", "   "]})


def test_blank_scalar_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Profile.model_validate({**VALID, "seniority": "  "})


def test_list_entries_are_stripped() -> None:
    profile = Profile.model_validate({**VALID, "core_skills": ["  SQL  ", "dbt"]})
    assert profile.core_skills == ["SQL", "dbt"]


def test_unknown_field_is_rejected() -> None:
    """extra="forbid": a typo'd key must fail, not be silently ignored — a
    profile that quietly drops half its content still scores every posting."""
    with pytest.raises(ValidationError):
        Profile.model_validate({**VALID, "core_skils": ["SQL"]})


def test_render_is_deterministic() -> None:
    profile = Profile.model_validate(VALID)
    assert render_prompt(profile) == render_prompt(profile)


def test_render_preserves_declared_order() -> None:
    """Order expresses priority, so it must survive rendering unsorted."""
    profile = Profile.model_validate({**VALID, "core_skills": ["dbt", "SQL", "Airflow"]})
    rendered = render_prompt(profile)
    assert rendered.index("- dbt") < rendered.index("- SQL") < rendered.index("- Airflow")


def test_render_carries_the_four_measured_rules() -> None:
    """The rubric encodes docs/research/relevance-signals.md. Losing a rule
    silently reverts V2 to the keyword matcher that document disproved, so the
    prompt is asserted rather than assumed."""
    rendered = render_prompt(Profile.model_validate(VALID))
    assert "REQUIREMENTS" in rendered
    assert "eligibility" in rendered
    assert "DESCRIBED SCOPE" in rendered
    assert "LOWERS the score" in rendered


def test_render_includes_every_profile_field() -> None:
    rendered = render_prompt(Profile.model_validate(VALID))
    for value in ("Analytics Engineer", "SQL", "Airflow", "no relocation"):
        assert value in rendered
    assert "senior individual contributor" in rendered
    assert "Builds and maintains analytics pipelines" in rendered


def test_empty_optional_list_renders_a_placeholder() -> None:
    """Never emit a bare heading with nothing under it — an empty section reads
    to the model as a truncated prompt."""
    profile = Profile.model_validate({**VALID, "constraints": []})
    assert "- (none stated)" in render_prompt(profile)


def test_prompt_version_is_set() -> None:
    assert PROMPT_VERSION


def test_load_reads_the_private_file(tmp_path: Path) -> None:
    profile = load_profile(_write(tmp_path, VALID))
    assert profile.seniority == "senior individual contributor"


def test_load_falls_back_to_the_example_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """CI and a fresh clone have no private profile; they must still build."""
    with caplog.at_level(logging.WARNING):
        profile = load_profile(str(tmp_path / "absent.yaml"))
    assert profile.target_roles
    assert "using the example" in caplog.text


def test_load_rejects_a_non_mapping(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must contain a YAML mapping"):
        load_profile(_write(tmp_path, ["not", "a", "mapping"]))


def test_load_rejects_a_malformed_profile(tmp_path: Path) -> None:
    """Fails before any posting is scored, not partway through a billed run."""
    with pytest.raises(ValidationError):
        load_profile(_write(tmp_path, {**VALID, "core_skills": []}))


def test_relative_path_resolves_against_the_repo_root() -> None:
    """Settings holds a repo-relative default, but the pipeline is not always
    invoked from the repo root — resolve against ROOT, not the cwd."""
    profile = load_profile("config/profile.example.yaml")
    assert profile.core_skills


def test_missing_profile_and_missing_example_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scoring against a silently absent profile would rate every posting
    against nothing, so the last resort is a failure, not a default."""
    monkeypatch.setattr("shared.profile.EXAMPLE_PROFILE", tmp_path / "no-example.yaml")
    with pytest.raises(FileNotFoundError, match="no profile at"):
        load_profile(str(tmp_path / "absent.yaml"))


def test_committed_example_is_valid() -> None:
    """The example is the CI fallback, so a broken one breaks every fork."""
    profile = load_profile(str(EXAMPLE_PROFILE))
    assert profile.core_skills
    assert render_prompt(profile)
