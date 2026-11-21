"""Landing the rendered scoring prompt for dbt to read."""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import pytest
import yaml

from ingest import land_resume
from shared import storage
from shared.config import Settings
from shared.resume import PROFILE_VERSION, PROMPT_VERSION, EvidenceUnit


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
    # 6 bullets + 1 project + the one other wording the example gives a bullet.
    assert len([u for u in units if u[1] == "work"]) == 8
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


def _edited(
    tmp_path: Path,
    *,
    bullets: list[str],
    variants: list[str] | None = None,
    summary: str = "Builds analytics pipelines.",
) -> Settings:
    """Settings over a resume written to disk: the given bullets in one role,
    with `variants` as other wordings of the first."""
    entries: list[dict[str, object]] = [{"text": text} for text in bullets]
    if variants:
        entries[0]["variants"] = variants
    (tmp_path / "resume.yaml").write_text(
        yaml.safe_dump(
            {
                "summary": summary,
                "seniority": "senior individual contributor",
                "work_history": [
                    {
                        "org": "Example Corp",
                        "title": "Analyst",
                        "period": "2020 to 2024",
                        "bullets": entries,
                    }
                ],
            }
        )
    )
    return Settings(
        pipeline_target="dev",
        duckdb_path=str(tmp_path / "jobs.duckdb"),
        resume_yaml=str(tmp_path / "resume.yaml"),
    )


def _variant_rows(settings: Settings) -> list[tuple[str, str]]:
    con = duckdb.connect(settings.duckdb_path)
    try:
        return [
            (str(parent), str(text))
            for parent, text in con.execute(
                "select variant_of, text from resume_units where variant_of is not null"
            ).fetchall()
        ]
    finally:
        con.close()


def test_a_variant_lands_for_the_matcher_and_moves_neither_version(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """What makes a rewording free: it reaches the embedding units, and the two
    texts a model reads -- which version every stored result -- do not move, so
    the next run re-scores and re-assesses nothing."""
    land_resume.run(_edited(tmp_path, bullets=["Built one thing."]))

    settings = _edited(tmp_path, bullets=["Built one thing."], variants=["Shipped a thing."])
    with caplog.at_level(logging.INFO, logger="land_resume"):
        assert land_resume.run(settings) == 0

    assert _variant_rows(settings) == [("Example Corp:2020 to 2024:0", "Shipped a thing.")]
    assert len(_rows(settings)) == 1
    assert len(_profiles(settings)) == 1
    assert "0 bullet(s) added, 0 removed; 1 variant(s) added, 0 removed" in caplog.text
    assert "no text a model reads changed" in caplog.text


def test_a_new_bullet_moves_both_versions_and_says_what_it_re_runs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    land_resume.run(_edited(tmp_path, bullets=["Built one thing."]))

    settings = _edited(tmp_path, bullets=["Built one thing.", "Built another."])
    with caplog.at_level(logging.INFO, logger="land_resume"):
        assert land_resume.run(settings) == 0

    assert len(_rows(settings)) == 2
    assert len(_profiles(settings)) == 2
    assert "1 bullet(s) added, 0 removed" in caplog.text
    assert "are re-scored and re-assessed on the next run" in caplog.text


def test_an_edit_outside_the_bullets_is_named_as_one(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A changed summary re-runs every posting exactly as a new bullet does, and
    without this line the report would say nothing changed and then bill."""
    land_resume.run(_edited(tmp_path, bullets=["Built one thing."]))

    settings = _edited(tmp_path, bullets=["Built one thing."], summary="A different summary.")
    with caplog.at_level(logging.INFO, logger="land_resume"):
        land_resume.run(settings)

    assert "0 bullet(s) added, 0 removed" in caplog.text
    assert "the change is outside them" in caplog.text


def _stored_results(settings: Settings, *, scope: int, scored: int, assessed: int) -> None:
    """The three silver relations a check counts over: `scope` posting texts a
    re-pass may touch, of which the first `scored` and `assessed` hold a result,
    plus one stored result outside the scope on each side."""
    con = duckdb.connect(settings.duckdb_path)
    try:
        con.execute("create schema if not exists main_silver")
        for name, rows in (
            ("int_jobs_repass_scope", [f"h{i}" for i in range(scope)]),
            ("int_jobs_scored", [f"h{i}" for i in range(scored)] + ["closed"]),
            ("int_jobs_assessed", [f"h{i}" for i in range(assessed)] + ["closed"]),
        ):
            con.execute(f"create table main_silver.{name} (content_hash varchar)")
            if rows:  # executemany rejects an empty batch
                con.executemany(f"insert into main_silver.{name} values (?)", [[r] for r in rows])
    finally:
        con.close()


def test_check_reports_the_re_run_and_lands_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The preview is only useful if it is the number that would be billed and
    costs nothing to ask for: stored results inside the re-pass scope, with the
    landed prompt, profile and units all left as they were."""
    before = _edited(tmp_path, bullets=["Built one thing."])
    land_resume.run(before)
    _stored_results(before, scope=3, scored=3, assessed=2)
    landed = (_rows(before), _profiles(before), _units(before))

    settings = _edited(tmp_path, bullets=["Built one thing.", "Built another."])
    with caplog.at_level(logging.INFO, logger="land_resume"):
        assert land_resume.run(settings, check=True) == 0

    assert "landing this would re-score 3 posting text(s)" in caplog.text
    assert "landing this would re-assess 2 posting text(s)" in caplog.text
    assert "check only: nothing landed" in caplog.text
    assert (_rows(settings), _profiles(settings), _units(settings)) == landed


def test_check_counts_only_the_pass_whose_text_moved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The scorer and the assessment read different texts, so one can move
    without the other, and a count for the pass that will not run is a bill
    that will not arrive."""
    settings = _edited(tmp_path, bullets=["Built one thing."])
    land_resume.run(settings)
    _stored_results(settings, scope=3, scored=3, assessed=2)

    monkeypatch.setattr(land_resume, "render_profile", lambda _r: "a reworded profile layout")
    with caplog.at_level(logging.INFO, logger="land_resume"):
        assert land_resume.run(settings, check=True) == 0

    assert "landing this would re-assess 2 posting text(s)" in caplog.text
    assert "would re-score" not in caplog.text


def test_check_on_a_variant_only_edit_counts_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Nothing would be re-run, so there is nothing to count -- and no silver
    table is read, which is why this passes with none built."""
    land_resume.run(_edited(tmp_path, bullets=["Built one thing."]))

    settings = _edited(tmp_path, bullets=["Built one thing."], variants=["Shipped a thing."])
    with caplog.at_level(logging.INFO, logger="land_resume"):
        assert land_resume.run(settings, check=True) == 0

    assert "would re-score" not in caplog.text
    assert "check only: nothing landed" in caplog.text
    assert _variant_rows(settings) == []


def test_check_on_a_fresh_target_provisions_the_sources_and_lands_no_rows(tmp_path: Path) -> None:
    settings = _edited(tmp_path, bullets=["Built one thing."])
    _stored_results(settings, scope=0, scored=0, assessed=0)

    assert land_resume.run(settings, check=True) == 0

    assert _rows(settings) == []
    assert _profiles(settings) == []
    assert _units(settings) == []


def _unit(text: str, *, variant_of: str | None = None) -> EvidenceUnit:
    return EvidenceUnit(
        unit_id=text, source="a role", text=text, evidences=[], variant_of=variant_of
    )


def test_unit_change_counts_an_edited_bullet_as_removed_and_added() -> None:
    """Compared by text because ids are positional: an id comparison would call
    every bullet after an inserted one changed."""
    landed: list[dict[str, object]] = [
        {"unit_id": "r:0", "text": "kept", "kind": "work", "variant_of": None},
        {"unit_id": "r:1", "text": "old wording", "kind": "work", "variant_of": None},
        {"unit_id": "r:0:v0", "text": "dropped variant", "kind": "work", "variant_of": "r:0"},
        {"unit_id": "industry:x", "text": "kept", "kind": "industry", "variant_of": None},
    ]

    change = land_resume.unit_change(
        landed,
        [_unit("inserted first"), _unit("kept"), _unit("new wording")],
        [_unit("new variant", variant_of="kept")],
    )

    assert change == land_resume.UnitChange(
        first_landing=False,
        bullets_added=2,
        bullets_removed=1,
        variants_added=1,
        variants_removed=1,
    )


def test_unit_change_reads_a_table_from_before_variants_existed() -> None:
    """Such a table has neither `kind` nor `variant_of`, and held bullets only."""
    landed: list[dict[str, object]] = [{"unit_id": "r:0", "text": "kept"}]

    change = land_resume.unit_change(landed, [_unit("kept")], [])

    assert change == land_resume.UnitChange(False, 0, 0, 0, 0)


def test_unit_change_with_nothing_landed_is_a_first_landing() -> None:
    assert land_resume.unit_change([], [_unit("a")], []).first_landing


def test_report_names_only_the_pass_whose_text_moved() -> None:
    change = land_resume.UnitChange(False, 1, 0, 0, 0)

    lines = land_resume.change_report(change, prompt_changed=False, profile_changed=True)

    assert any("are re-assessed on the next run" in line for line in lines)
    assert not any("re-scored" in line for line in lines)


def test_report_on_a_first_landing_does_not_guess_at_a_diff() -> None:
    change = land_resume.UnitChange(True, 3, 0, 0, 0)

    lines = land_resume.change_report(change, prompt_changed=True, profile_changed=True)

    assert lines[0] == "resume: no earlier landing to compare with"
    assert not any("outside them" in line for line in lines)


def test_main_passes_the_check_flag_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []

    def fake_run(settings: Settings | None = None, *, check: bool = False) -> int:
        seen.append(check)
        return 0

    monkeypatch.setattr(land_resume, "run", fake_run)

    assert land_resume.main([]) == 0
    assert land_resume.main(["--check"]) == 0
    assert seen == [False, True]


def _profiles(settings: Settings) -> list[tuple[str, str]]:
    con = duckdb.connect(settings.duckdb_path)
    try:
        return [
            (str(v), str(p))
            for v, p in con.execute(
                "select prompt_version, rendered_prompt from assessment_profile"
            ).fetchall()
        ]
    finally:
        con.close()


def test_lands_the_assessment_profile_in_its_own_table(tmp_path: Path) -> None:
    """Its own table, because each consumer takes "the newest row": in one
    table, the scorer would be handed the profile as its prompt."""
    settings = _settings(tmp_path)

    assert land_resume.run(settings) == 0

    ((version, profile),) = _profiles(settings)
    assert version.startswith(f"{PROFILE_VERSION}.")
    assert profile.startswith("Summary:")
    # The scorer still gets its own prompt, rubric and all, and not this text.
    assert "IGNORE THE POSTING" not in profile
    assert all("IGNORE THE POSTING" in prompt for _, prompt in _rows(settings))


def test_profile_lands_once_and_again_only_when_it_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    land_resume.run(settings)
    land_resume.run(settings)
    assert len(_profiles(settings)) == 1

    monkeypatch.setattr(land_resume, "render_profile", lambda _r: "an edited set of bullets")
    land_resume.run(settings)

    assert len(_profiles(settings)) == 2
    # Editing the profile must re-assess, and must not re-score: the scoring
    # prompt did not move, so no second scoring row is landed.
    assert len(_rows(settings)) == 1


def test_a_changed_scoring_prompt_still_lands_the_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two are landed independently: neither branch may return early and
    skip the other."""
    settings = _settings(tmp_path)
    monkeypatch.setattr(land_resume, "render_prompt", lambda _r: "reworded prompt")
    land_resume.run(settings)
    assert len(_rows(settings)) == 1
    assert len(_profiles(settings)) == 1
