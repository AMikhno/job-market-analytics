"""The Pages site build: ADR parsing, doc staging, manifest facts, rendering.

The link rewriting carries most of the risk. The docs are moved into a new layout
on the way to the site, and a link re-pointed wrongly does not fail anything -- it
publishes a page that 404s. So each path a link can take is pinned here.
"""

import re
import struct
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from scripts import build_site as bs
from shared.models import MarketSnapshot

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
        "site/landing/assets/favicon.svg": "<svg/>\n",
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
    assert (docs / "assets" / "favicon.svg").exists()  # one favicon, shared with the landing
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
    # Lineage nodes and ADR titles are truncated with an ellipsis; hover shows them whole.
    assert 'title="fct_y · table">fct_y</a>' in out
    assert 'title="raw_a">raw_a</a>' in out
    assert 'title="Use dbt &amp; &lt;b&gt;"' in out


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


def test_the_phone_menu_carries_the_same_links_as_the_top_bar() -> None:
    """The menu repeats the nav's markup, so a link added to one can miss the other."""
    template = (bs.SITE_SRC / "landing" / "index.html").read_text()
    bar, menu = re.findall(r'<nav aria-label="Site">(.*?)</nav>', template, re.S)
    assert re.findall(r'href="([^"]+)"', bar) == re.findall(r'href="([^"]+)"', menu)


def test_broken_local_links(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "index.html").write_text('<h2 id="x">X</h2>')
    (tmp_path / "lineage").mkdir()
    (tmp_path / "lineage" / "index.html").write_text("")
    (tmp_path / "a.css").write_text("")
    (tmp_path / "index.html").write_text(
        '<a href="docs/">ok</a><a href="a.css">ok</a><a href="docs/#x">ok</a>'
        '<a href="https://x.y/z">ext</a><a href="#top">anchor</a>'
        '<a href="lineage/#!/model/m">client-side route</a>'
        '<a href="docs/#renamed">missing id</a><a href="gone.html">missing</a>'
        '<a href="gone/#!/model/m">missing</a>'
    )
    assert bs.broken_local_links(tmp_path, "index.html") == [
        "docs/#renamed",
        "gone.html",
        "gone/#!/model/m",
    ]


def test_link_preview_tags_point_at_real_assets() -> None:
    """Crawlers fetch og:image once and cache it, so a wrong size or a dead URL
    shows as a blank card on every share until the cache expires."""
    template = (bs.SITE_SRC / "landing" / "index.html").read_text()
    image = re.search(r'property="og:image" content="([^"]+)"', template)
    assert image and image.group(1).startswith("https://")
    png = bs.SITE_SRC / "landing" / "assets" / image.group(1).rsplit("/", 1)[1]
    header = png.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", header[16:24]) == (1200, 630)
    assert 'content="1200"' in template and 'content="630"' in template
    assert (bs.SITE_SRC / "landing" / "assets" / "favicon.svg").exists()


# ------------------------------------------------------------------- market


def _snapshot(**overrides: Any) -> MarketSnapshot:
    fields: dict[str, Any] = {
        "generated_at": datetime(2026, 10, 13, 12, 0, tzinfo=UTC),
        "min_companies": 5,
        "daily": [
            {
                "observed_on": date(2026, 10, 1),
                "boards": 60,
                "open_postings": 900,
                "new_postings": 0,
                "companies": 50,
            },
            {
                "observed_on": date(2026, 10, 7),
                "boards": 60,
                "open_postings": 950,
                "new_postings": 30,
                "companies": 51,
            },
            {
                "observed_on": date(2026, 10, 13),
                "boards": 80,
                "open_postings": 1000,
                "new_postings": 20,
                "companies": 52,
            },
        ],
        "tenure": [
            {
                "days_on_board": 3,
                "at_risk": 100,
                "at_risk_companies": 40,
                "closed": 30,
                "survival": 0.7,
            },
            {
                "days_on_board": 9,
                "at_risk": 60,
                "at_risk_companies": 30,
                "closed": 20,
                "survival": 0.47,
            },
        ],
        "roles": [
            {
                "role_family": "Other",
                "open_postings": 800,
                "companies": 50,
                "remote_postings": 1,
                "hybrid_postings": 1,
                "onsite_postings": 1,
                "unstated_postings": 797,
            },
            {
                "role_family": "Analytics <x>",
                "open_postings": 40,
                "companies": 9,
                "remote_postings": 10,
                "hybrid_postings": 2,
                "onsite_postings": 0,
                "unstated_postings": 28,
            },
        ],
        "tech": [
            {
                "tech": "dbt",
                "category": "Transformation",
                "postings": 20,
                "companies": 9,
                "share_of_postings": 0.25,
            }
        ],
        "ats": [{"ats": "greenhouse", "open_postings": 700, "boards": 30}],
    } | overrides
    return MarketSnapshot.model_validate(fields)


def test_median_days_up_is_where_the_curve_reaches_one_half() -> None:
    assert bs.median_days_up(_snapshot().tenure) == 9
    assert bs.median_days_up(_snapshot(tenure=[]).tenure) is None


def test_render_market_without_a_snapshot_says_so() -> None:
    assert "omitted" in bs.render_market(None)


def test_render_market_tiles_figures_and_escaping() -> None:
    out = bs.render_market(_snapshot())
    assert 'tile-n">1,000<' in out  # latest open postings
    assert 'tile-n">50<' in out  # new in the 7 days Oct 7-13 inclusive; Oct 1 is outside
    assert 'tile-n">9 days<' in out and "median 9 days" in out
    assert out.count('<figure class="viz">') == 5
    assert "Analytics &lt;x&gt;" in out and "<x>" not in out
    # "Other" is in the table view but not drawn as a bar among the data families
    assert out.count(">Other<") == 1
    assert "fewer than 5 companies" in out


def test_render_market_stacks_short_charts_over_long_ones() -> None:
    out = bs.render_market(_snapshot())
    first, left, right = out.split('<div class="viz-col">')
    assert "Open postings per tracked board" in first
    assert "How long a posting stays up" in left and "Data roles by family" in left
    assert "What data roles ask for" in right and "Where the postings come from" in right


def test_render_market_draws_every_chart_for_phones_too() -> None:
    out = bs.render_market(_snapshot())
    assert out.count('class="chart-wide"') == out.count('class="chart-narrow"') == 5


def test_render_market_names_each_ats_and_puts_other_last() -> None:
    ats = [
        {"ats": "greenhouse", "open_postings": 700, "boards": 30},
        {"ats": "other", "open_postings": 400, "boards": 6},
        {"ats": "bamboohr", "open_postings": 100, "boards": 5},
        {"ats": "newats", "open_postings": 50, "boards": 5},
    ]
    out = bs.render_market(_snapshot(ats=ats))
    table = out.split("<th>ATS</th>")[1]
    order = [table.index(f"<td>{n}</td>") for n in ("Greenhouse", "BambooHR", "newats", "Other")]
    assert order == sorted(order)
    assert "fewer than 5 boards each, combined" in out


def test_render_market_before_any_posting_has_closed() -> None:
    out = bs.render_market(_snapshot(tenure=[]))
    assert "Not enough closed postings yet" in out and 'tile-n">—<' in out


def test_load_market_round_trips_and_tolerates_absence(tmp_path: Path) -> None:
    path = tmp_path / "market.json"
    assert bs.load_market(path) is None
    path.write_text(_snapshot().model_dump_json())
    snapshot = bs.load_market(path)
    assert snapshot is not None and snapshot.daily[-1].open_postings == 1000
