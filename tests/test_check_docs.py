"""The docs checker has to be trustworthy in both directions.

A checker that misses drift is useless; one that cries wolf gets switched off,
which is the same outcome. So both halves are tested: real breakage is caught,
and the shapes that merely *look* like paths are not flagged.
"""

import pytest

from scripts import check_docs


@pytest.mark.parametrize(
    "token",
    [
        "ingest/pipeline.py",
        "docs/decisions/0013-broaden-v1-ingestion.md",
        "dbt/models/sources.yml",
    ],
)
def test_recognizes_real_repo_paths(token: str) -> None:
    assert check_docs._looks_like_path(token)


@pytest.mark.parametrize(
    "token",
    [
        "https://example.com/a/b",  # URL
        "/apply/jobs.json",  # URL path fragment, not a repo path
        "{board_ref}",  # a template placeholder
        "[^\"']+",  # a regex
        "root|app|__next",  # a regex alternation
        "active=false",  # a config value
        "board_ref",  # a plain identifier
    ],
)
def test_ignores_things_that_are_not_paths(token: str) -> None:
    assert not check_docs._looks_like_path(token)


def test_markdown_links_are_only_parsed_in_markdown() -> None:
    """`](` occurs inside Python character classes. Scanning .py for markdown
    link syntax reported three regexes as broken links."""
    line = "re.findall(r'href=[\"\\']([^\"\\']+)[\"\\']', html)"
    refs = check_docs._candidate_refs(line, markdown=False)
    assert [r for r in refs if r.startswith("LINK")] == []


def test_a_missing_path_is_reported(tmp_path, monkeypatch) -> None:
    doc = check_docs.ROOT / "README.md"
    problem = check_docs._check_ref(
        "x.md",
        1,
        "PATH:ingest/does_not_exist.py",
        targets=set(),
        models=set(),
        adrs=set(),
        source=doc,
    )
    assert problem is not None and problem.why == "path does not exist"


def test_an_existing_path_is_not_reported() -> None:
    assert (
        check_docs._check_ref(
            "x.md",
            1,
            "PATH:ingest/pipeline.py",
            targets=set(),
            models=set(),
            adrs=set(),
            source=check_docs.ROOT / "README.md",
        )
        is None
    )


def test_paths_resolve_relative_to_the_referring_file() -> None:
    """A README naming a sibling file is correct from where it sits and nonsense
    from the repo root, so resolution has to try the referring file's directory."""
    source = check_docs.ROOT / "tools" / "probes" / "README.md"
    resolved = check_docs._resolve("careers-tail/probe_bamboohr.py", source=source)
    assert resolved is not None and resolved.exists()


def test_dbt_paths_resolve_from_the_dbt_project_root() -> None:
    """CLAUDE.md names `macros/cross_db.sql` the way dbt itself does."""
    resolved = check_docs._resolve("macros/cross_db.sql", source=check_docs.ROOT / "CLAUDE.md")
    assert resolved is not None and resolved.exists()


def test_an_adr_can_be_addressed_by_its_number_prefix() -> None:
    resolved = check_docs._resolve("docs/decisions/0013", source=check_docs.ROOT / "README.md")
    assert resolved is not None and resolved.exists()


def test_a_symbol_reference_checks_only_the_file() -> None:
    """`shared/models.py:RawPosting` names a class, not line RawPosting."""
    resolved = check_docs._resolve("shared/models.py:RawPosting", source=check_docs.ROOT / "R.md")
    assert resolved is not None and resolved.exists()


def test_unknown_make_target_is_reported() -> None:
    problem = check_docs._check_ref(
        "x.md",
        1,
        "MAKE:not-a-target",
        targets={"test"},
        models=set(),
        adrs=set(),
        source=check_docs.ROOT / "README.md",
    )
    assert problem is not None and "make target" in problem.why


def test_unknown_dbt_model_is_reported() -> None:
    problem = check_docs._check_ref(
        "x.md",
        1,
        "DBT:int_nope",
        targets=set(),
        models={"silver_jobs"},
        adrs=set(),
        source=check_docs.ROOT / "README.md",
    )
    assert problem is not None and "dbt model" in problem.why


def test_a_marker_listing_several_facts_is_parsed() -> None:
    """One comment can tag several facts. An earlier regex required exactly
    `<!-- check:name -->`, so a multi-name comment matched nothing and the check
    passed silently -- green meaning "found no claims" is the worst failure a
    gate can have."""
    line = "9 sources, 24 ADRs <!-- check:sources check:adrs -->"
    assert check_docs._fact_names(line) == ["sources", "adrs"]


def test_a_wrong_count_is_reported(tmp_path, monkeypatch) -> None:
    doc = tmp_path / "d.md"
    doc.write_text("There are 11 sources. <!-- check:sources -->\n")
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    (problem,) = check_docs._check_facts("d.md", {"sources": 9})
    assert "does not state the real value (9)" in problem.why


def test_a_correct_count_is_accepted(tmp_path, monkeypatch) -> None:
    doc = tmp_path / "d.md"
    doc.write_text("There are 9 sources. <!-- check:sources -->\n")
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    assert check_docs._check_facts("d.md", {"sources": 9}) == []


def test_an_unknown_fact_name_is_reported(tmp_path, monkeypatch) -> None:
    """A typo in the marker must fail, not silently check nothing."""
    doc = tmp_path / "d.md"
    doc.write_text("Whatever. <!-- check:nonesuch -->\n")
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    (problem,) = check_docs._check_facts("d.md", {"sources": 9})
    assert problem.why == "no such computed fact"


def test_raw_table_names_must_match_a_registered_source(tmp_path, monkeypatch) -> None:
    doc = tmp_path / "d.md"
    doc.write_text("We land into raw_lever_jobs and raw_nosuch_jobs.\n")
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    (problem,) = check_docs._check_raw_tables("d.md", {"lever", "ashby"})
    assert problem.ref == "raw_nosuch_jobs"


def test_facts_are_computed_from_the_code() -> None:
    """The counts must come from the code, not a hand-maintained list."""
    from ingest.sources import SOURCES

    assert check_docs.facts()["sources"] == len(SOURCES)


def test_the_repo_itself_passes() -> None:
    """The gate must hold for the tree as committed, or it is decorative."""
    assert check_docs.main() == 0
