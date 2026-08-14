"""The private-terms hook has to catch leaks without becoming noise.

A hook that cries wolf gets bypassed with --no-verify, which is the same as not
having one, so both directions are tested.
"""

import pytest

from scripts import check_private_terms as cpt


def test_matches_a_term_on_a_word_boundary() -> None:
    hits = cpt.scan("We poll Northwind's board nightly.\n", {"northwind": "company"})
    assert hits == [(1, "northwind", "company")]


def test_is_case_insensitive() -> None:
    assert cpt.scan("NORTHWIND\n", {"northwind": "company"})


def test_does_not_match_inside_a_longer_word() -> None:
    """Substring matching would fire on half the English language."""
    assert cpt.scan("northwindsurfing is unrelated\n", {"northwind": "company"}) == []


def test_reports_the_right_line() -> None:
    text = "clean\nclean\nNorthwind appears here\n"
    assert cpt.scan(text, {"northwind": "company"}) == [(3, "northwind", "company")]


def test_no_terms_means_the_check_is_skipped_not_passed(monkeypatch, capsys) -> None:
    """In CI or a fresh clone there is no company list. Failing open is correct;
    doing it silently would look like a pass that verified something."""
    monkeypatch.setattr(cpt, "_terms", dict)
    assert cpt.main(["anything.md"]) == 0
    assert "check skipped" in capsys.readouterr().err


def test_a_leak_fails_the_commit(tmp_path, monkeypatch, capsys) -> None:
    doc = tmp_path / "d.md"
    doc.write_text("Northwind's board is polled every run.\n")
    monkeypatch.setattr(cpt, "_terms", lambda: {"northwind": "company"})

    assert cpt.main([str(doc)]) == 1

    err = capsys.readouterr().err
    assert "d.md:1" in err and "northwind" in err


def test_clean_content_passes(tmp_path, monkeypatch) -> None:
    doc = tmp_path / "d.md"
    doc.write_text("A two-word Ashby ref is percent-encoded into the path.\n")
    monkeypatch.setattr(cpt, "_terms", lambda: {"northwind": "company"})
    assert cpt.main([str(doc)]) == 0


@pytest.mark.parametrize("word", ["github", "render", "canonical", "shell", "users"])
def test_ordinary_words_are_skipped(word: str) -> None:
    """Several real company names are also everyday English or tech words.
    Matching them would fire on nearly every commit."""
    assert word in cpt.SKIP


def test_the_term_sources_are_never_scanned(tmp_path, monkeypatch) -> None:
    """The company list contains every term by definition; scanning it would
    report the whole file."""
    monkeypatch.setattr(cpt, "_terms", lambda: {"northwind": "company"})
    monkeypatch.setattr(cpt, "COMPANIES", tmp_path / "companies.csv")
    cpt.COMPANIES.write_text("company_name\nNorthwind\n")
    assert cpt.main([str(cpt.COMPANIES)]) == 0


def test_the_repo_itself_is_clean() -> None:
    """The tracked tree must contain no private identifier. This is the check
    that would have caught the ~60 that reached the repo before it existed."""
    import subprocess

    files = subprocess.run(
        ["git", "ls-files", "*.md", "*.py", "*.yml", "*.toml", "*.sql"],
        cwd=cpt.ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert cpt.main([str(cpt.ROOT / f) for f in files]) == 0
