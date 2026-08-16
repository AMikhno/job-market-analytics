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
    assert version == PROMPT_VERSION
    assert "IGNORE THE POSTING'S TITLE" in prompt


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
