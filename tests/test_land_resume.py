"""Landing the rendered scoring prompt for dbt to read."""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import pytest

from ingest import land_resume
from shared import storage
from shared.config import Settings
from shared.resume import PROMPT_VERSION


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        pipeline_target="dev",
        duckdb_path=str(tmp_path / "jobs.duckdb"),
        # No private resume in a test tree, so the loader falls back to the
        # committed example — which is the CI path too.
        resume_yaml=str(tmp_path / "absent.yaml"),
    )


def _rows(settings: Settings) -> list[tuple[str, str]]:
    con = duckdb.connect(settings.duckdb_path)
    try:
        return [
            (str(v), str(p))
            for v, p in con.execute(
                "select prompt_version, rendered_prompt from scoring_prompt"
            ).fetchall()
        ]
    finally:
        con.close()


def test_lands_the_rendered_prompt(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    assert land_resume.run(settings) == 0

    rows = _rows(settings)
    assert len(rows) == 1
    version, prompt = rows[0]
    assert version.startswith(f"{PROMPT_VERSION}.")
    assert "IGNORE THE POSTING'S TITLE" in prompt


def test_a_new_resume_lands_a_new_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression this exists for: PROMPT_VERSION tracks the rubric wording,
    so replacing the resume produces a different prompt under an unchanged
    version — and int_jobs_scored, which re-scores when prompt_version moves,
    would skip every posting. A new corpus would have had no effect at all."""
    settings = _settings(tmp_path)
    land_resume.run(settings)
    first = _rows(settings)[0][0]

    monkeypatch.setattr(land_resume, "render_prompt", lambda _r: "a different resume entirely")
    land_resume.run(settings)

    versions = [v for v, _ in _rows(settings)]
    assert len(set(versions)) == 2, "a changed resume must not reuse the version"
    assert all(v.startswith(f"{PROMPT_VERSION}.") for v in versions)
    assert first in versions


def test_running_twice_lands_one_row(tmp_path: Path) -> None:
    """The prompt is landed before every scoring build, so a no-op re-run must
    not accumulate rows — the scored model reads the newest one and duplicates
    would make "newest" arbitrary between identical candidates."""
    settings = _settings(tmp_path)

    land_resume.run(settings)
    land_resume.run(settings)

    assert len(_rows(settings)) == 1


def test_a_changed_prompt_lands_a_new_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A wording change must land, because that is what tells re-scoring the
    prompt moved. Landing append-only also keeps the old prompt readable, so
    "which wording produced this score" stays answerable."""
    settings = _settings(tmp_path)
    land_resume.run(settings)

    monkeypatch.setattr(land_resume, "render_prompt", lambda _resume: "reworded prompt")
    with caplog.at_level(logging.INFO):
        assert land_resume.run(settings) == 0

    rows = _rows(settings)
    assert len(rows) == 2
    assert "reworded prompt" in [p for _, p in rows]
    assert "not comparable" in caplog.text


def test_first_run_provisions_the_table(tmp_path: Path) -> None:
    """dbt reads this as a source, and a missing source fails the whole DAG
    rather than the one model that needs it."""
    settings = _settings(tmp_path)

    land_resume.run(settings)

    assert storage.scoring_prompt_table(settings) == "scoring_prompt"
    assert _rows(settings)


def test_prod_table_name_is_fully_qualified() -> None:
    settings = Settings(pipeline_target="prod", gcp_project="proj", bq_dataset="jobs")
    assert storage.scoring_prompt_table(settings) == "proj.jobs_ops.scoring_prompt"


def test_reports_role_family_coverage(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """The count per family is the corpus gap made visible before a scoring run."""
    settings = _settings(tmp_path)

    with caplog.at_level(logging.INFO, logger="land_resume"):
        assert land_resume.run(settings) == 0

    coverage = [r.getMessage() for r in caplog.records if "role-family coverage" in r.getMessage()]
    assert len(coverage) == 1
    # The example corpus tags every declared family, so none should be at zero.
    assert "analytics-engineer" in coverage[0]
    assert "product-analyst" in coverage[0]
    assert not [r for r in caplog.records if "no bullets tagged" in r.getMessage()]


def _units(settings: Settings) -> list[tuple[str, str, str]]:
    con = duckdb.connect(settings.duckdb_path)
    try:
        return [
            (str(i), str(k), str(t))
            for i, k, t in con.execute(
                "select unit_id, kind, text from resume_units order by unit_id"
            ).fetchall()
        ]
    finally:
        con.close()


def test_lands_work_bullets_and_industries_as_separate_kinds(tmp_path: Path) -> None:
    """The matcher joins on `kind`: requirements against work bullets, the
    employer sentence against industries. The example names one industry on two
    roles, which must land once."""
    settings = _settings(tmp_path)

    assert land_resume.run(settings) == 0

    units = _units(settings)
    industries = [text for _, kind, text in units if kind == "industry"]
    assert industries == ["B2B SaaS"]
    assert len([u for u in units if u[1] == "work"]) == 7  # 6 bullets + 1 project
    assert {kind for _, kind, _ in units} == {"work", "industry"}


def test_relanding_replaces_the_units(tmp_path: Path) -> None:
    """Replaced, never appended: a bullet removed from the resume must stop
    being matched, and a second landing must not double every unit."""
    settings = _settings(tmp_path)

    land_resume.run(settings)
    first = _units(settings)
    land_resume.run(settings)

    assert _units(settings) == first


def test_a_units_table_from_before_the_kind_column_is_replaced(tmp_path: Path) -> None:
    """A table provisioned with the earlier, narrower shape would reject every
    insert. Landing rebuilds it instead of requiring someone to drop it by hand."""
    settings = _settings(tmp_path)
    con = duckdb.connect(settings.duckdb_path)
    try:
        con.execute(
            "create table resume_units (unit_id varchar, source varchar, text varchar, "
            "evidences varchar, prompt_version varchar)"
        )
        con.execute("insert into resume_units values ('stale', 's', 't', '', 'v0')")
    finally:
        con.close()

    assert land_resume.run(settings) == 0

    units = _units(settings)
    assert "stale" not in [unit_id for unit_id, _, _ in units]
    assert {kind for _, kind, _ in units} == {"work", "industry"}


def test_warns_when_no_role_names_an_industry(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Without one, industry_similarity is null for every posting, which reads
    exactly like the matcher being switched off."""
    resume = tmp_path / "resume.yaml"
    resume.write_text(
        "summary: Builds analytics pipelines.\n"
        "seniority: senior individual contributor\n"
        "work_history:\n"
        "  - org: Example Corp\n"
        "    title: Analyst\n"
        "    period: 2020 to 2024\n"
        "    bullets:\n"
        "      - text: Built one thing.\n"
    )
    settings = Settings(
        pipeline_target="dev",
        duckdb_path=str(tmp_path / "jobs.duckdb"),
        resume_yaml=str(resume),
    )

    with caplog.at_level(logging.INFO, logger="land_resume"):
        assert land_resume.run(settings) == 0

    assert "no role names an industry" in caplog.text
    assert [kind for _, kind, _ in _units(settings)] == ["work"]


def test_warns_when_a_family_has_no_bullets(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A silent zero is indistinguishable from those roles being a poor fit."""
    resume = tmp_path / "resume.yaml"
    resume.write_text(
        "summary: Builds analytics pipelines.\n"
        "seniority: senior individual contributor\n"
        "work_history:\n"
        "  - org: Example Corp\n"
        "    title: Analyst\n"
        "    period: 2020 to 2024\n"
        "    bullets:\n"
        "      - text: Built one thing.\n"
        "        evidences: [analytics-engineer]\n"
    )
    settings = Settings(
        pipeline_target="dev",
        duckdb_path=str(tmp_path / "jobs.duckdb"),
        resume_yaml=str(resume),
    )

    with caplog.at_level(logging.INFO, logger="land_resume"):
        assert land_resume.run(settings) == 0

    warnings = [r.getMessage() for r in caplog.records if "no bullets tagged" in r.getMessage()]
    assert len(warnings) == 1
    assert "product-analyst" in warnings[0]
    assert "analytics-engineer" not in warnings[0]
