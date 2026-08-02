import json
from pathlib import Path

from ingest import report_run
from shared.models import RunSummary, SourceSummary


def _env(monkeypatch, tmp_path, summary: RunSummary | None) -> Path:
    """Point the reporter at a summary file (or a path with no file)."""
    path = tmp_path / "summary.json"
    if summary is not None:
        path.write_text(summary.model_dump_json())
    monkeypatch.setenv("SUMMARY_PATH", str(path))
    step_summary = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    return step_summary


def test_a_clean_run_reports_nothing(monkeypatch, tmp_path, capsys) -> None:
    """Silence is the healthy case: an annotation on every run trains the reader
    to ignore annotations."""
    step_summary = _env(
        monkeypatch,
        tmp_path,
        RunSummary(
            sources=[SourceSummary(source="lever", rows=400, status="ok", company_count=11)]
        ),
    )

    assert report_run.main() == 0

    assert capsys.readouterr().out == ""
    assert not step_summary.exists()


def test_all_three_conditions_are_reported(monkeypatch, tmp_path, capsys) -> None:
    step_summary = _env(
        monkeypatch,
        tmp_path,
        RunSummary(
            warnings=["pinpoint"],
            sources=[
                SourceSummary(
                    source="ashby",
                    rows=1400,
                    status="ok",
                    company_count=56,
                    skipped_refs=["redacted:ad589ceb"],
                )
            ],
            unconfigured=["bamboohr", "rippling"],
        ),
    )

    assert report_run.main() == 0

    out = capsys.readouterr().out
    assert "::warning::Low/zero volume from: pinpoint" in out
    assert "::warning::Skipped boards (redacted, use `make whois REF=…`): redacted:ad589ceb" in out
    assert "::warning::Sources with no active boards" in out
    assert "bamboohr, rippling" in out
    # the same three, in markdown, appended to the step summary
    written = step_summary.read_text()
    assert written.count(":warning:") == 3
    assert "pinpoint" in written and "redacted:ad589ceb" in written and "bamboohr" in written


def test_skipped_refs_are_never_raw(monkeypatch, tmp_path, capsys) -> None:
    """This text lands in a public Actions log. The pipeline redacts refs before
    they reach the summary; the reporter must not undo that."""
    _env(
        monkeypatch,
        tmp_path,
        RunSummary(
            sources=[
                SourceSummary(source="ashby", rows=1, status="ok", skipped_refs=["redacted:ff01"])
            ]
        ),
    )

    assert report_run.main() == 0

    out = capsys.readouterr().out
    assert "redacted:ff01" in out
    assert "dominion" not in out.lower()


def test_step_summary_is_appended_not_truncated(monkeypatch, tmp_path, capsys) -> None:
    """$GITHUB_STEP_SUMMARY accumulates across steps -- opening it 'w' would
    silently drop whatever an earlier step wrote."""
    step_summary = _env(monkeypatch, tmp_path, RunSummary(warnings=["lever"]))
    step_summary.write_text("earlier step output\n")

    assert report_run.main() == 0

    assert step_summary.read_text().startswith("earlier step output\n")
    assert "lever" in step_summary.read_text()


def test_a_missing_summary_warns_and_still_succeeds(monkeypatch, tmp_path, capsys) -> None:
    _env(monkeypatch, tmp_path, None)

    assert report_run.main() == 0  # never fails the run it reports on

    assert "no ingest run summary was found" in capsys.readouterr().out


def test_a_malformed_summary_warns_and_still_succeeds(monkeypatch, tmp_path, capsys) -> None:
    """The reporter runs after rows are already landed. A summary it cannot
    parse is worth seeing, but it is not a pipeline failure."""
    path = tmp_path / "summary.json"
    path.write_text("{not json at all")
    monkeypatch.setenv("SUMMARY_PATH", str(path))

    assert report_run.main() == 0

    assert "could not read the ingest run summary" in capsys.readouterr().out


def test_notices_are_absent_when_nothing_is_wrong() -> None:
    assert report_run.notices(RunSummary()) == []


def test_no_step_summary_env_still_prints_annotations(monkeypatch, tmp_path, capsys) -> None:
    """Run locally (`uv run python -m ingest.report_run`) there is no
    $GITHUB_STEP_SUMMARY; the annotations must still reach stdout."""
    path = tmp_path / "summary.json"
    path.write_text(RunSummary(warnings=["lever"]).model_dump_json())
    monkeypatch.setenv("SUMMARY_PATH", str(path))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    assert report_run.main() == 0

    assert "::warning::Low/zero volume from: lever" in capsys.readouterr().out


def test_reads_a_summary_the_pipeline_actually_wrote(monkeypatch, tmp_path, capsys) -> None:
    """Guards the seam between the two modules: the reporter parses the file
    ingest/pipeline.py writes, not a hand-built approximation of it."""
    path = tmp_path / "summary.json"
    monkeypatch.setenv("SUMMARY_PATH", str(path))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    from ingest import pipeline
    from shared.config import get_settings

    pipeline._write_summary(
        get_settings(),
        run_id="abc",
        failures=[],
        warnings=["pinpoint"],
        runs=[],
        unconfigured=["bamboohr"],
    )

    assert json.loads(path.read_text())["unconfigured"] == ["bamboohr"]
    assert report_run.main() == 0
    out = capsys.readouterr().out
    assert "pinpoint" in out and "bamboohr" in out
