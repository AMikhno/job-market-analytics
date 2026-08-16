"""Resume corpus loading, validation, evidence units, and prompt rendering."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from shared.resume import (
    EXAMPLE_RESUME,
    PROMPT_VERSION,
    ROLE_FAMILIES,
    Resume,
    evidence_units,
    load_resume,
    render_prompt,
)

VALID: dict[str, object] = {
    "summary": "Builds and maintains analytics pipelines end to end.",
    "seniority": "senior individual contributor",
    "constraints": ["no relocation"],
    "skills": {"core_stack": ["SQL", "dbt"]},
    "work_history": [
        {
            "org": "Example Corp",
            "title": "Senior Analyst",
            "official_title": "Software Engineer II",
            "period": "2020-01 to 2024-06",
            "bullets": [
                {
                    "text": "Built a governed metric model CS ran renewals on.",
                    "evidences": ["analytics-engineer"],
                },
                {
                    "text": "Unified CRM and product usage into a funnel.",
                    "evidences": ["gtm-engineer"],
                },
            ],
        }
    ],
    "projects": [
        {"name": "Side Project", "text": "A pipeline.", "evidences": ["analytics-engineer"]}
    ],
    "education": ["B.S. Computer Science, 2014 - Institution."],
}


def _write(tmp_path: Path, data: object) -> str:
    path = tmp_path / "resume.yaml"
    path.write_text(yaml.safe_dump(data))
    return str(path)


def test_valid_resume_parses() -> None:
    resume = Resume.model_validate(VALID)
    assert resume.work_history[0].org == "Example Corp"
    assert len(resume.work_history[0].bullets) == 2


def test_optional_sections_default_empty() -> None:
    minimal = {k: v for k, v in VALID.items() if k in ("summary", "seniority", "work_history")}
    resume = Resume.model_validate(minimal)
    assert resume.projects == []
    assert resume.education == []
    assert resume.skills == {}


def test_work_history_cannot_be_empty() -> None:
    """The corpus IS the work history; an empty one scores against nothing."""
    with pytest.raises(ValidationError):
        Resume.model_validate({**VALID, "work_history": []})


def test_role_needs_at_least_one_bullet() -> None:
    role = {**VALID["work_history"][0], "bullets": []}  # type: ignore[index]
    with pytest.raises(ValidationError):
        Resume.model_validate({**VALID, "work_history": [role]})


def test_unknown_role_family_is_rejected() -> None:
    """A typo'd tag would silently tag nothing, and a matcher quietly ignoring
    half the corpus looks exactly like one that works."""
    role = {**VALID["work_history"][0]}  # type: ignore[dict-item]
    role["bullets"] = [{"text": "x", "evidences": ["gtm-enginer"]}]
    with pytest.raises(ValidationError, match="unknown role families"):
        Resume.model_validate({**VALID, "work_history": [role]})


def test_unknown_role_family_rejected_on_projects() -> None:
    bad = [{"name": "P", "text": "t", "evidences": ["data-wizard"]}]
    with pytest.raises(ValidationError, match="unknown role families"):
        Resume.model_validate({**VALID, "projects": bad})


def test_every_declared_family_is_accepted() -> None:
    role = {**VALID["work_history"][0]}  # type: ignore[dict-item]
    role["bullets"] = [{"text": "x", "evidences": sorted(ROLE_FAMILIES)}]
    resume = Resume.model_validate({**VALID, "work_history": [role]})
    assert set(resume.work_history[0].bullets[0].evidences) == set(ROLE_FAMILIES)


def test_bullet_text_is_whitespace_normalised() -> None:
    """YAML block scalars arrive with embedded newlines; the prompt wants one
    line per bullet so the rendered list stays parseable by the model."""
    role = {**VALID["work_history"][0]}  # type: ignore[dict-item]
    role["bullets"] = [{"text": "built   a\n  thing\n"}]
    resume = Resume.model_validate({**VALID, "work_history": [role]})
    assert resume.work_history[0].bullets[0].text == "built a thing"


def test_blank_bullet_is_rejected() -> None:
    role = {**VALID["work_history"][0]}  # type: ignore[dict-item]
    role["bullets"] = [{"text": "   \n  "}]
    with pytest.raises(ValidationError):
        Resume.model_validate({**VALID, "work_history": [role]})


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Resume.model_validate({**VALID, "summry": "typo"})


def test_evidence_units_are_bullet_level() -> None:
    """One unit per bullet, not one per resume — a single vector over the whole
    document averages unrelated work into something matching everything weakly."""
    units = evidence_units(Resume.model_validate(VALID))
    assert len(units) == 3  # 2 bullets + 1 project
    assert units[0].text.startswith("Built a governed metric model")


def test_evidence_unit_ids_are_unique_and_stable() -> None:
    resume = Resume.model_validate(VALID)
    first = [u.unit_id for u in evidence_units(resume)]
    assert len(set(first)) == len(first)
    assert first == [u.unit_id for u in evidence_units(resume)]


def test_evidence_unit_carries_its_origin() -> None:
    """A bullet read without knowing where it happened loses the "for whom",
    which is what separates the role families."""
    units = evidence_units(Resume.model_validate(VALID))
    assert "Senior Analyst at Example Corp" in units[0].source
    assert units[-1].source.startswith("Personal project")


def test_render_is_deterministic() -> None:
    resume = Resume.model_validate(VALID)
    assert render_prompt(resume) == render_prompt(resume)


def test_render_instructs_the_model_to_ignore_titles() -> None:
    """The whole redesign rests on this: titles name the org chart, not the
    work. Asserted so a future prompt edit cannot quietly drop it."""
    rendered = render_prompt(Resume.model_validate(VALID))
    assert "IGNORE THE POSTING'S TITLE" in rendered


def test_render_carries_the_rubric_rules() -> None:
    rendered = render_prompt(Resume.model_validate(VALID))
    assert "REQUIREMENTS" in rendered
    assert "eligibility" in rendered
    assert "DESCRIBED SCOPE" in rendered
    assert "LOWERS the score" in rendered


def test_render_includes_the_work_not_just_the_summary() -> None:
    rendered = render_prompt(Resume.model_validate(VALID))
    assert "Built a governed metric model" in rendered
    assert "Unified CRM and product usage" in rendered
    assert "A pipeline." in rendered


def test_render_includes_constraints_and_skills() -> None:
    rendered = render_prompt(Resume.model_validate(VALID))
    assert "no relocation" in rendered
    assert "SQL, dbt" in rendered


def test_render_handles_a_resume_with_no_optional_sections() -> None:
    minimal = {k: v for k, v in VALID.items() if k in ("summary", "seniority", "work_history")}
    rendered = render_prompt(Resume.model_validate(minimal))
    assert "- (none stated)" in rendered
    assert "Education" not in rendered


def test_prompt_version_is_set() -> None:
    assert PROMPT_VERSION


def test_load_reads_the_private_file(tmp_path: Path) -> None:
    resume = load_resume(_write(tmp_path, VALID))
    assert resume.seniority == "senior individual contributor"


def test_load_falls_back_to_the_example_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """CI and a fresh clone have no private resume; they must still build."""
    with caplog.at_level(logging.WARNING):
        resume = load_resume(str(tmp_path / "absent.yaml"))
    assert resume.work_history
    assert "using the example" in caplog.text


def test_relative_path_resolves_against_the_repo_root() -> None:
    resume = load_resume("config/resume.example.yaml")
    assert resume.work_history


def test_load_rejects_a_non_mapping(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must contain a YAML mapping"):
        load_resume(_write(tmp_path, ["not", "a", "mapping"]))


def test_load_rejects_a_malformed_resume(tmp_path: Path) -> None:
    """Fails before any posting is scored, not partway through a billed run."""
    with pytest.raises(ValidationError):
        load_resume(_write(tmp_path, {**VALID, "work_history": []}))


def test_missing_resume_and_missing_example_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shared.resume.EXAMPLE_RESUME", tmp_path / "no-example.yaml")
    with pytest.raises(FileNotFoundError, match="no resume at"):
        load_resume(str(tmp_path / "absent.yaml"))


def test_committed_example_is_valid() -> None:
    """The example is the CI fallback, so a broken one breaks every fork."""
    resume = load_resume(str(EXAMPLE_RESUME))
    assert evidence_units(resume)
    assert render_prompt(resume)
