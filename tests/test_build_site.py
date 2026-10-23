"""The Pages site build: ADR parsing, doc staging, manifest facts, rendering.

The link rewriting carries most of the risk. The docs are moved into a new layout
on the way to the site, and a link re-pointed wrongly does not fail anything -- it
publishes a page that 404s. So each path a link can take is pinned here.
"""

from pathlib import Path
from typing import Any

import pytest

from scripts import build_site as bs

REPO_FILES = {
    "ARCHITECTURE.md",
    "README.md",
    "docs/v2-plan.md",
    "docs/decisions/0001-first.md",
    "docs/decisions/0002-second.md",
    "docs/research/probe.md",
    "ingest/pipeline.py",
    "tools/probes/README.md",
}


def _adr(number: str, stem: str, **kw: Any) -> bs.Adr:
    fields: dict[str, Any] = {
        "number": number,
        "title": f"Title {number}",
        "stem": stem,
        "status": "accepted",
        "supersedes": (),
        "superseded_by": None,
        "added": None,
    } | kw
    return bs.Adr(**fields)


ADRS = {
    "0001": _adr("0001", "0001-first"),
    "0002": _adr("0002", "0002-second"),
}


# ------------------------------------------------------------------- ADRs


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_parse_adr_reads_number_title_and_status(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "0007-x.md",
        "# 0007 — Secrets boundary\n\n**Status:** accepted\n\nBody.\n",
    )
    adr = bs.parse_adr(path, added="2026-06-17")
    assert (adr.number, adr.title, adr.status, adr.added) == (
        "0007",
        "Secrets boundary",
        "accepted",
        "2026-06-17",
    )


def test_parse_adr_superseded_names_its_successor(tmp_path: Path) -> None:
    path = _write(tmp_path / "0009-x.md", "# 0009 — Old\n\n**Status:** superseded by 0025 (why)\n")
    adr = bs.parse_adr(path)
    assert adr.status == "superseded" and adr.superseded_by == "0025"


def test_parse_adr_superseded_in_part(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "0005-x.md", "# 0005 — Old\n\n**Status:** superseded in part by ADR-0013\n"
    )
    assert bs.parse_adr(path).superseded_by == "0013"


def test_parse_adr_reads_supersedes_across_a_wrapped_status(tmp_path: Path) -> None:
    """A status line can wrap; the paragraph is read whole, up to the blank line."""
    path = _write(
        tmp_path / "0025-x.md",
        "# 0025 — New\n\n**Status:** accepted (for V2;\nsupersedes 0009)\n\nBody mentions 0001.\n",
    )
    adr = bs.parse_adr(path)
    assert adr.status == "accepted" and adr.supersedes == ("0009",)


def test_parse_adr_proposed(tmp_path: Path) -> None:
    path = _write(tmp_path / "0017-x.md", "# 0017 — Maybe\n\n**Status:** proposed / evaluating\n")
    assert bs.parse_adr(path).status == "proposed"


def test_parse_adr_without_a_status_is_an_error(tmp_path: Path) -> None:
    path = _write(tmp_path / "0003-x.md", "# 0003 — No status\n\nBody.\n")
    with pytest.raises(ValueError, match="Status"):
        bs.parse_adr(path)


def test_every_real_adr_parses() -> None:
    """The decision log is generated, so a new ADR in the wrong shape must fail here."""
    adrs = bs.load_adrs(bs.ROOT, {})
    assert adrs and all(a.title and a.status for a in adrs)


def test_added_dates_keep_the_earliest_commit() -> None:
    log = "@2026-07-28\n\ndocs/decisions/0002-b.md\n@2026-06-17\n\ndocs/decisions/0002-b.md\n"
    assert bs.parse_added_dates(log) == {"docs/decisions/0002-b.md": "2026-06-17"}


# -------------------------------------------------------------- staging map


@pytest.mark.parametrize(
    ("repo_path", "staged"),
    [
        ("ARCHITECTURE.md", "architecture.md"),
        ("docs/v2-plan.md", "v2-plan.md"),
        ("docs/decisions/0001-first.md", "decisions/0001-first.md"),
        ("docs/research/probe.md", "research/probe.md"),
        ("index.md", "index.md"),
        ("README.md", None),
        ("docs/decisionsX/a.md", None),
    ],
)
def test_staged_path(repo_path: str, staged: str | None) -> None:
    assert bs.staged_path(repo_path) == staged


def test_page_url_uses_directory_urls() -> None:
    assert bs.page_url("docs/decisions/0001-first.md") == "docs/decisions/0001-first/"
    assert bs.page_url("index.md") == "docs/"


def test_page_url_rejects_unstaged_files() -> None:
    with pytest.raises(ValueError):
        bs.page_url("README.md")


# ----------------------------------------------------------- link rewriting


def test_a_link_between_staged_pages_follows_the_move() -> None:
    text = "See [arch](../../ARCHITECTURE.md#v2)."
    out = bs.rewrite_links(text, "docs/decisions/0001-first.md", REPO_FILES)
    assert out == "See [arch](../architecture.md#v2)."


def test_a_link_from_the_home_page_resolves_from_the_repo_root() -> None:
    out = bs.rewrite_links("[a](docs/research/probe.md)", "index.md", REPO_FILES)
    assert out == "[a](research/probe.md)"


def test_a_link_to_an_unstaged_file_goes_to_github() -> None:
    out = bs.rewrite_links(
        "[code](../../ingest/pipeline.py)", "docs/decisions/0001-first.md", REPO_FILES
    )
    assert out == f"[code]({bs.REPO_URL}/blob/main/ingest/pipeline.py)"


def test_a_link_to_a_directory_goes_to_the_github_tree() -> None:
    out = bs.rewrite_links("[probes](../tools/probes)", "docs/v2-plan.md", REPO_FILES)
    assert out == f"[probes]({bs.REPO_URL}/tree/main/tools/probes)"


def test_a_dead_link_is_left_for_the_strict_build_to_fail_on() -> None:
    text = "[gone](missing.md)"
    assert bs.rewrite_links(text, "docs/v2-plan.md", REPO_FILES) == text


@pytest.mark.parametrize(
    "text", ["[x](https://example.com/a.md)", "[x](#anchor)", "[x](mailto:a@b.c)"]
)
def test_absolute_and_anchor_links_are_untouched(text: str) -> None:
    assert bs.rewrite_links(text, "docs/v2-plan.md", REPO_FILES) == text


def test_links_inside_fenced_code_are_untouched() -> None:
    text = "```\n[x](../ARCHITECTURE.md)\n```\n"
    assert bs.rewrite_links(text, "docs/v2-plan.md", REPO_FILES) == text


def test_a_link_with_a_code_label_is_rewritten_whole() -> None:
    out = bs.rewrite_links("[`arch`](../ARCHITECTURE.md)", "docs/v2-plan.md", REPO_FILES)
    assert out == "[`arch`](architecture.md)"


# ------------------------------------------------------------- ADR links


def test_a_bare_adr_reference_becomes_a_link() -> None:
    out = bs.link_adr_refs("Per ADR-0002, yes.", "ARCHITECTURE.md", ADRS)
    assert out == "Per [ADR-0002](decisions/0002-second.md), yes."


def test_adr_links_are_relative_to_the_page() -> None:
    out = bs.link_adr_refs("ADR-0002", "docs/decisions/0001-first.md", ADRS)
    assert out == "[ADR-0002](0002-second.md)"


@pytest.mark.parametrize(
    "text",
    [
        "`ADR-0002`",  # inline code
        "```\nADR-0002\n```\n",  # fenced code
        "[see ADR-0002](x.md)",  # already inside a link
        # Not among ADRS. A number with no ADR is reported by the docs check, and
        # linking it here anyway would hide that. (A real number, so the docs
        # check -- which scans this file too -- does not flag the test itself.)
        "ADR-0003",
    ],
)
def test_adr_references_that_must_not_be_linked(text: str) -> None:
    assert bs.link_adr_refs(text, "ARCHITECTURE.md", ADRS) == text


def test_an_adr_does_not_link_to_itself() -> None:
    assert bs.link_adr_refs("ADR-0001", "docs/decisions/0001-first.md", ADRS) == "ADR-0001"


def test_text_around_an_existing_link_is_still_linked() -> None:
    out = bs.link_adr_refs("[a](b.md) and ADR-0001", "ARCHITECTURE.md", ADRS)
    assert out == "[a](b.md) and [ADR-0001](decisions/0001-first.md)"


def test_adr_index_is_newest_first_and_names_successors() -> None:
    adrs = [
        _adr("0001", "0001-first", status="superseded", superseded_by="0002"),
        _adr("0002", "0002-second", added="2026-07-01"),
    ]
    rows = [line for line in bs.adr_index(adrs).splitlines() if line.startswith("| 0")]
    assert rows[0].startswith("| 0002") and "2026-07-01" in rows[0]
    assert "superseded by ADR-0002" in rows[1]


# ----------------------------------------------------------------- staging


def _repo(root: Path) -> None:
    files = {
        "ARCHITECTURE.md": "# Arch\n\nSee ADR-0001.\n",
        "docs/v2-plan.md": "# Plan\n\n[arch](../ARCHITECTURE.md)\n",
        "docs/decisions/0001-first.md": "# 0001 — First\n\n**Status:** accepted\n",
        "README.md": "# not staged\n",
        "site/docs/index.md": "# Home\n\n[arch](ARCHITECTURE.md)\n",
        "site/docs/assets/extra.css": "body {}\n",
        "site/zensical.toml": "[project]\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def test_stage_docs_writes_a_complete_zensical_project(tmp_path: Path) -> None:
    _repo(tmp_path)
    tracked = {"ARCHITECTURE.md", "docs/v2-plan.md", "docs/decisions/0001-first.md", "README.md"}
    adrs = bs.load_adrs(tmp_path, {})
    dest = tmp_path / "build"
    (dest / "stale").mkdir(parents=True)

    pages = bs.stage_docs(tmp_path, dest, adrs, tracked)

    docs = dest / "docs"
    assert pages == 5  # three staged files, the home page, the generated decision index
    assert not (dest / "stale").exists()
    assert (dest / "zensical.toml").exists() and (docs / "assets" / "extra.css").exists()
    assert not (docs / "README.md").exists()
    assert "[ADR-0001](decisions/0001-first.md)" in (docs / "architecture.md").read_text()
    assert "[arch](architecture.md)" in (docs / "v2-plan.md").read_text()
    assert "[arch](architecture.md)" in (docs / "index.md").read_text()
    assert "0001-first.md" in (docs / "decisions" / "index.md").read_text()


# ----------------------------------------------------------------- manifest


def _manifest() -> dict[str, Any]:
    def model(name: str, zone: str, mat: str, enforced: bool = False) -> dict[str, Any]:
        return {
            "resource_type": "model",
            "name": name,
            "fqn": ["proj", zone, name],
            "config": {"materialized": mat, "contract": {"enforced": enforced}},
        }

    return {
        "nodes": {
            "model.proj.stg_a": model("stg_a", "bronze", "view"),
            "model.proj.silver_x": model("silver_x", "silver", "table", enforced=True),
            "model.proj.fct_y": model("fct_y", "gold", "table", enforced=True),
            "test.proj.t1": {"resource_type": "test"},
            "test.proj.t2": {"resource_type": "test"},
            "seed.proj.s": {"resource_type": "seed"},
        },
        "sources": {"source.proj.raw.raw_a": {"name": "raw_a"}},
        "exposures": {"exposure.proj.digest": {"name": "digest", "label": "Digest <b>"}},
        "unit_tests": {"unit_test.proj.u1": {}},
    }


def test_lineage_nodes_are_placed_by_zone_in_dag_order() -> None:
    nodes = bs.lineage_nodes(_manifest())
    assert [(n.zone, n.name) for n in nodes] == [
        ("sources", "raw_a"),
        ("bronze", "stg_a"),
        ("silver", "silver_x"),
        ("gold", "fct_y"),
        ("exposures", "digest"),
    ]


def test_a_model_outside_the_three_zones_is_an_error() -> None:
    manifest = _manifest()
    manifest["nodes"]["model.proj.odd"] = {
        "resource_type": "model",
        "name": "odd",
        "fqn": ["proj", "marts", "odd"],
        "config": {},
    }
    with pytest.raises(ValueError, match="outside"):
        bs.lineage_nodes(manifest)


def test_compute_stats_counts_from_the_manifest() -> None:
    stats = bs.compute_stats(_manifest(), python_tests=7, adrs=3, doc_pages=9)
    assert (stats.models, stats.sources, stats.exposures) == (3, 1, 1)
    assert (stats.data_tests, stats.unit_tests, stats.contracts) == (2, 1, 2)
    assert stats.materializations == {"view": 1, "table": 2}


def test_count_python_tests(tmp_path: Path) -> None:
    (tmp_path / "test_a.py").write_text(
        "def test_one(): ...\nasync def test_two(): ...\ndef helper(): ...\n"
        "class TestX:\n    def test_three(self): ...\n"
    )
    (tmp_path / "conftest.py").write_text("def test_not_collected(): ...\n")
    assert bs.count_python_tests(tmp_path) == 3


# ------------------------------------------------------------------ landing


def test_render_landing_fills_and_escapes() -> None:
    manifest = _manifest()
    manifest["sources"]["source.proj.raw.<x>"] = {"name": "<script>"}
    stats = bs.compute_stats(manifest, python_tests=1, adrs=1, doc_pages=1)
    template = (
        "$terminal|$stat_cards|$lineage|$decision_log|$adr_count|$commit|$commit_url|$built_on"
    )
    out = bs.render_landing(
        template,
        stats=stats,
        nodes=bs.lineage_nodes(manifest),
        adrs=[_adr("0001", "0001-first", title="Use `dbt` & <b>")],
        commit="abc1234",
    )
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "<code>dbt</code> &amp; &lt;b&gt;" in out
    assert 'href="lineage/#!/model/model.proj.fct_y"' in out
    assert 'href="docs/decisions/0001-first/"' in out


def test_render_landing_fails_on_an_unknown_placeholder() -> None:
    stats = bs.compute_stats(_manifest(), python_tests=1, adrs=0, doc_pages=1)
    with pytest.raises(KeyError):
        bs.render_landing("$nope", stats=stats, nodes=[], adrs=[], commit="x")


def test_the_real_landing_template_renders() -> None:
    """Catches a placeholder added to the template but not to the renderer."""
    template = (bs.SITE_SRC / "landing" / "index.html").read_text()
    stats = bs.compute_stats(_manifest(), python_tests=1, adrs=0, doc_pages=1)
    out = bs.render_landing(template, stats=stats, nodes=[], adrs=[], commit="abc1234")
    assert "${" not in out


def test_broken_local_links(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "index.html").write_text("")
    (tmp_path / "a.css").write_text("")
    (tmp_path / "index.html").write_text(
        '<a href="docs/">ok</a><a href="a.css">ok</a><a href="docs/#x">ok</a>'
        '<a href="https://x.y/z">ext</a><a href="#top">anchor</a>'
        '<a href="lineage/#!/model/m">missing</a><a href="gone.html">missing</a>'
    )
    assert bs.broken_local_links(tmp_path, "index.html") == ["lineage/#!/model/m", "gone.html"]
