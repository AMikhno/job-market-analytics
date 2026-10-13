"""Build the GitHub Pages site into `_site/`: a landing page, the docs, and dbt lineage.

    /          site/landing/ -- every number on it is read at build time from the
               dbt manifest, the ADRs and the test suite, so none of them is typed
               by hand and none of them can rot;
    /docs/     ARCHITECTURE.md, the ADRs, the research notes and the V2 plan,
               rendered by Zensical from the repo's own markdown -- one home per fact;
    /lineage/  dbt docs, exactly as `make dbt-docs` produces them.

The docs are **staged**, not rendered in place, because the generator wants every
page under one directory and the repo keeps its markdown in two (the root and
docs/). Staging also flattens `docs/decisions/` to `decisions/`, so a page is not
served from `/docs/docs/…`. Moving files breaks the relative links they contain,
so every link is re-resolved against the file's *original* location and pointed
at the page's new one; a link that leaves the staged set goes to GitHub instead.

The build fails rather than publishes a broken site: Zensical runs `--strict`, so
a dead link in the docs aborts it, and the landing page's own links are checked
against the assembled output before anything is written for upload.
"""

from __future__ import annotations

import ast
import html
import json
import posixpath
import re
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SITE_SRC = ROOT / "site"
BUILD = ROOT / "build" / "site"
OUT = ROOT / "_site"
REPO_URL = "https://github.com/AMikhno/job-market-analytics"

# Repo path (file or directory prefix) -> where it is staged under the docs root.
STAGE_MAP = {
    "ARCHITECTURE.md": "architecture.md",
    "docs/v2-plan.md": "v2-plan.md",
    "docs/decisions/": "decisions/",
    "docs/research/": "research/",
}
# The docs home is tracked as site/docs/index.md but written as if it sat at the
# repo root, so its links are checkable by scripts/check_docs.py *and* stageable.
HOME = "index.md"

# Lineage columns, in DAG order. The manifest's fqn names the zone directory.
ZONES = ("sources", "bronze", "silver", "gold", "exposures")


# --------------------------------------------------------------------------- ADRs


@dataclass(frozen=True)
class Adr:
    number: str
    title: str
    stem: str
    status: str  # accepted | superseded | proposed
    supersedes: tuple[str, ...]
    superseded_by: str | None
    added: str | None  # ISO date of the commit that added the file

    @property
    def repo_path(self) -> str:
        return f"docs/decisions/{self.stem}.md"


_ADR_TITLE = re.compile(r"^#\s+(\d{4})\s+—\s+(.+?)\s*$", re.M)
_ADR_STATUS = re.compile(r"^\*\*Status:\*\*(.*?)(?:\n\s*\n|\Z)", re.M | re.S)


def parse_adr(path: Path, added: str | None = None) -> Adr:
    """Read an ADR's number, title and status from the file itself.

    Raises on a file missing either line: an ADR that cannot say what it decided
    and whether that still holds is a broken document, not one to skip.
    """
    text = path.read_text()
    title = _ADR_TITLE.search(text)
    status = _ADR_STATUS.search(text)
    if not title or not status:
        raise ValueError(f"{path.name}: needs a '# NNNN — Title' heading and a **Status:** line")
    raw = " ".join(status.group(1).split()).lower()
    kind = (
        "superseded"
        if raw.startswith("superseded")
        else "proposed"
        if raw.startswith("proposed")
        else "accepted"
    )
    by = re.search(r"superseded (?:in part )?by (?:adr-)?(\d{4})", raw)
    supersedes = re.findall(r"supersedes[^.;)]*?(?:adr-)?(\d{4})", raw)
    return Adr(
        number=title.group(1),
        title=title.group(2),
        stem=path.stem,
        status=kind,
        supersedes=tuple(sorted(set(supersedes))),
        superseded_by=by.group(1) if by else None,
        added=added,
    )


def parse_added_dates(log: str) -> dict[str, str]:
    """`git log --diff-filter=A --format=@%as --name-only` -> {path: first-added date}.

    Log order is newest first, so the last date seen for a path is the earliest.
    """
    dates: dict[str, str] = {}
    current = ""
    for line in log.splitlines():
        if line.startswith("@"):
            current = line[1:]
        elif line.strip() and current:
            dates[line.strip()] = current
    return dates


def added_dates(root: Path) -> dict[str, str]:
    """When each ADR was added, from git history; empty in a shallow clone.

    A shallow clone's root commit appears to *add* every file in it, which would
    date all of them to today -- so no dates are better than wrong ones.
    """
    shallow = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if shallow == "true":
        return {}
    log = subprocess.run(
        ["git", "log", "--diff-filter=A", "--format=@%as", "--name-only", "--", "docs/decisions"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return parse_added_dates(log)


def load_adrs(root: Path, dates: dict[str, str]) -> list[Adr]:
    paths = sorted((root / "docs" / "decisions").glob("[0-9][0-9][0-9][0-9]-*.md"))
    return [parse_adr(p, dates.get(p.relative_to(root).as_posix())) for p in paths]


# ---------------------------------------------------------------- staging docs


def staged_path(repo_path: str) -> str | None:
    """Where a repo file lands under the docs root, or None if it is not staged."""
    if repo_path == HOME:
        return HOME
    for prefix, dest in STAGE_MAP.items():
        if repo_path == prefix or (prefix.endswith("/") and repo_path.startswith(prefix)):
            return dest + repo_path[len(prefix) :] if prefix.endswith("/") else dest
    return None


def page_url(repo_path: str) -> str:
    """Site-root-relative URL of a staged page (Zensical serves directory URLs)."""
    staged = staged_path(repo_path)
    if staged is None:
        raise ValueError(f"{repo_path} is not part of the docs")
    stem = staged.removesuffix(".md")
    return "docs/" if stem == "index" else f"docs/{stem}/"


_CODE = re.compile(r"(`[^`\n]*`)")
_LINK = re.compile(r"\[([^\]\n]*)\]\(([^)\s]+)\)")
_ADR_REF = re.compile(r"\bADR-(\d{4})\b")


def _prose(text: str, fn: Callable[[str], str], *, skip_inline_code: bool = True) -> str:
    """Apply fn to the text outside fenced code blocks (and inline code spans).

    Link rewriting passes skip_inline_code=False: a link whose label is code --
    [`file.md`](file.md) -- would otherwise be cut in half at the backticks.
    """
    out: list[str] = []
    fenced = False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            out.append(line)
        elif fenced:
            out.append(line)
        elif not skip_inline_code:
            out.append(fn(line))
        else:
            parts = _CODE.split(line)
            out.append("".join(p if i % 2 else fn(p) for i, p in enumerate(parts)))
    return "".join(out)


def _relative(target_staged: str, from_staged: str) -> str:
    return posixpath.relpath(target_staged, posixpath.dirname(from_staged) or ".")


def rewrite_links(text: str, repo_path: str, repo_files: set[str]) -> str:
    """Re-point relative links at the staged layout, or at GitHub if not staged.

    A link to something that exists nowhere is left alone on purpose: the strict
    build then fails on it, which is the right outcome for a dead link.
    """
    here = staged_path(repo_path)
    assert here is not None
    base = "" if repo_path == HOME else posixpath.dirname(repo_path)

    def repl(m: re.Match[str]) -> str:
        label, target = m.group(1), m.group(2)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            return m.group(0)
        path, sep, anchor = target.partition("#")
        resolved = posixpath.normpath(posixpath.join(base, path))
        staged = staged_path(resolved)
        if staged is not None and (resolved in repo_files or resolved == HOME):
            return f"[{label}]({_relative(staged, here)}{sep}{anchor})"
        if resolved in repo_files:
            return f"[{label}]({REPO_URL}/blob/main/{resolved}{sep}{anchor})"
        if any(f.startswith(resolved + "/") for f in repo_files):
            return f"[{label}]({REPO_URL}/tree/main/{resolved})"
        return m.group(0)

    return _prose(text, lambda s: _LINK.sub(repl, s), skip_inline_code=False)


def link_adr_refs(text: str, repo_path: str, adrs: dict[str, Adr]) -> str:
    """Turn every bare `ADR-0021` in prose into a link to that ADR's page.

    Skips code, text that is already a link, an ADR's references to itself, and
    numbers with no ADR -- the docs check reports those, so guessing here would
    only hide them.
    """
    here = staged_path(repo_path)
    assert here is not None

    def in_segment(segment: str) -> str:
        pieces = _LINK.split(segment)
        # re.split with two groups yields [text, label, target, text, ...]
        out: list[str] = []
        for i in range(0, len(pieces), 3):
            out.append(_ADR_REF.sub(adr_link, pieces[i]))
            if i + 2 < len(pieces):
                out.append(f"[{pieces[i + 1]}]({pieces[i + 2]})")
        return "".join(out)

    def adr_link(m: re.Match[str]) -> str:
        adr = adrs.get(m.group(1))
        if adr is None or adr.repo_path == repo_path:
            return m.group(0)
        target = staged_path(adr.repo_path)
        assert target is not None
        return f"[{m.group(0)}]({_relative(target, here)})"

    return _prose(text, in_segment)


def adr_index(adrs: list[Adr]) -> str:
    """The decisions landing page: every ADR, newest first, with its status."""
    lines = [
        "# Decisions",
        "",
        "Every non-obvious choice in this project has a record: what was decided, what it "
        "cost, and what it ruled out. Newest first; a superseded record stays, because the "
        "reasoning that was replaced is part of why the current answer is right.",
        "",
        "| | Decision | Status | Added |",
        "|---|---|---|---|",
    ]
    for adr in sorted(adrs, key=lambda a: a.number, reverse=True):
        status = adr.status
        if adr.superseded_by:
            status += f" by ADR-{adr.superseded_by}"
        lines.append(
            f"| {adr.number} | [{adr.title}]({adr.stem}.md) | {status} | {adr.added or ''} |"
        )
    return "\n".join(lines) + "\n"


def staged_sources(repo_files: Iterable[str]) -> list[str]:
    return sorted(f for f in repo_files if f.endswith(".md") and staged_path(f) is not None)


def stage_docs(root: Path, dest: Path, adrs: list[Adr], repo_files: set[str]) -> int:
    """Write the Zensical project to dest; returns the number of pages staged."""
    if dest.exists():
        shutil.rmtree(dest)
    docs = dest / "docs"
    by_number = {a.number: a for a in adrs}
    pages = [(f, (root / f).read_text()) for f in staged_sources(repo_files)]
    pages.append((HOME, (root / "site" / "docs" / "index.md").read_text()))
    for repo_path, text in pages:
        staged = staged_path(repo_path)
        assert staged is not None
        text = link_adr_refs(rewrite_links(text, repo_path, repo_files), repo_path, by_number)
        out = docs / staged
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    index = link_adr_refs(adr_index(adrs), "docs/decisions/index.md", by_number)
    (docs / "decisions" / "index.md").write_text(index)
    shutil.copytree(root / "site" / "docs" / "assets", docs / "assets")
    shutil.copy(root / "site" / "zensical.toml", dest / "zensical.toml")
    return len(pages) + 1


# -------------------------------------------------------------------- manifest


@dataclass(frozen=True)
class Node:
    unique_id: str
    name: str
    zone: str
    kind: str  # model | source | exposure
    materialized: str | None


@dataclass(frozen=True)
class Stats:
    models: int
    sources: int
    exposures: int
    data_tests: int
    unit_tests: int
    contracts: int
    materializations: dict[str, int]
    python_tests: int
    adrs: int
    doc_pages: int


def lineage_nodes(manifest: dict[str, Any]) -> list[Node]:
    """Sources, models and exposures, each placed in its lineage column."""
    nodes = [
        Node(uid, s["name"], "sources", "source", None) for uid, s in manifest["sources"].items()
    ]
    for uid, n in manifest["nodes"].items():
        if n["resource_type"] != "model":
            continue
        zone = n["fqn"][1] if len(n["fqn"]) > 2 else "silver"
        if zone not in ZONES:
            raise ValueError(f"{n['name']}: model outside bronze/silver/gold ({zone})")
        nodes.append(Node(uid, n["name"], zone, "model", n["config"].get("materialized")))
    nodes += [
        Node(uid, e["name"], "exposures", "exposure", None)
        for uid, e in manifest["exposures"].items()
    ]
    return sorted(nodes, key=lambda n: (ZONES.index(n.zone), n.name))


def count_python_tests(tests_dir: Path) -> int:
    """Test functions in the pytest suite (a parametrized one counts once)."""
    total = 0
    for path in tests_dir.rglob("test_*.py"):
        tree = ast.parse(path.read_text())
        total += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith("test_")
        )
    return total


def compute_stats(
    manifest: dict[str, Any], *, python_tests: int, adrs: int, doc_pages: int
) -> Stats:
    models = [n for n in manifest["nodes"].values() if n["resource_type"] == "model"]
    return Stats(
        models=len(models),
        sources=len(manifest["sources"]),
        exposures=len(manifest["exposures"]),
        data_tests=sum(1 for n in manifest["nodes"].values() if n["resource_type"] == "test"),
        unit_tests=len(manifest.get("unit_tests", {})),
        contracts=sum(1 for n in models if n["config"].get("contract", {}).get("enforced")),
        materializations=dict(Counter(n["config"].get("materialized") for n in models)),
        python_tests=python_tests,
        adrs=adrs,
        doc_pages=doc_pages,
    )


# ---------------------------------------------------------------- landing page


def inline_code(text: str) -> str:
    """Escape for HTML, keeping markdown `code` spans as <code>."""
    parts = re.split(r"`([^`]*)`", text)
    return "".join(
        f"<code>{html.escape(p)}</code>" if i % 2 else html.escape(p) for i, p in enumerate(parts)
    )


def render_stat_cards(stats: Stats) -> str:
    cards = [
        (stats.models, "dbt models", "bronze → silver → gold"),
        (stats.data_tests, "data tests", "schema + relationship contracts"),
        (stats.unit_tests, "dbt unit tests", "model logic on fixed inputs"),
        (stats.python_tests, "pytest functions", "behind an enforced coverage floor"),
        (stats.adrs, "decision records", "every non-obvious choice"),
    ]
    return "\n".join(
        f'<div class="stat"><span class="stat-n">{n}</span>'
        f'<span class="stat-label">{html.escape(label)}</span>'
        f'<span class="stat-note">{html.escape(note)}</span></div>'
        for n, label, note in cards
    )


def render_terminal(stats: Stats, commit: str, built_on: str) -> str:
    mats = " · ".join(f"{n} {m}" for m, n in sorted(stats.materializations.items()) if m)
    exposures = f"{stats.exposures} exposure{'' if stats.exposures == 1 else 's'}"
    rows = [
        ("manifest", f"{stats.models} models · {stats.sources} sources · {exposures}"),
        ("built as", mats),
        ("tests", f"{stats.data_tests} data · {stats.unit_tests} unit"),
        ("contracts", f"{stats.contracts} enforced"),
        ("docs", f"{stats.doc_pages} pages · 0 broken links"),
        ("commit", f"{commit} · {built_on}"),
    ]
    body = "\n".join(
        f'<span class="t-key">{html.escape(k)}</span>{html.escape(v)}' for k, v in rows
    )
    return f'<span class="t-cmd">make site</span>\n{body}'


def _lineage_href(node: Node) -> str:
    return f"lineage/#!/{node.kind}/{node.unique_id}"


def render_lineage(nodes: list[Node]) -> str:
    titles = {
        "sources": "raw",
        "bronze": "bronze",
        "silver": "silver",
        "gold": "gold",
        "exposures": "exposure",
    }
    columns = []
    for zone in ZONES:
        members = [n for n in nodes if n.zone == zone]
        items = "\n".join(
            f'<a class="node" href="{html.escape(_lineage_href(n))}"'
            + (f' title="{html.escape(n.materialized)}"' if n.materialized else "")
            + f">{html.escape(n.name)}</a>"
            for n in members
        )
        columns.append(
            f'<div class="zone zone-{zone}"><div class="zone-head">'
            f'<span class="zone-name">{titles[zone]}</span>'
            f'<span class="zone-count">{len(members)}</span></div>'
            f'<div class="zone-nodes">{items}</div></div>'
        )
    return "\n".join(columns)


def render_decision_log(adrs: list[Adr]) -> str:
    rows = []
    for adr in sorted(adrs, key=lambda a: a.number, reverse=True):
        note = ""
        if adr.superseded_by:
            note = f'<span class="adr-note">→ {adr.superseded_by}</span>'
        rows.append(
            f'<a class="adr adr-{adr.status}" href="{page_url(adr.repo_path)}">'
            f'<span class="adr-n">{adr.number}</span>'
            f'<span class="adr-title">{inline_code(adr.title)}</span>'
            f'<span class="adr-meta"><span class="pill pill-{adr.status}">{adr.status}</span>'
            f'{note}<span class="adr-date">{adr.added or ""}</span></span></a>'
        )
    return "\n".join(rows)


def render_landing(
    template: str, *, stats: Stats, nodes: list[Node], adrs: list[Adr], commit: str
) -> str:
    built_on = datetime.now(UTC).strftime("%Y-%m-%d")
    return Template(template).substitute(
        stat_cards=render_stat_cards(stats),
        terminal=render_terminal(stats, commit, built_on),
        lineage=render_lineage(nodes),
        decision_log=render_decision_log(adrs),
        adr_count=stats.adrs,
        commit=html.escape(commit),
        commit_url=f"{REPO_URL}/commit/{html.escape(commit)}",
        built_on=built_on,
    )


_HREF = re.compile(r'href="([^"]+)"')


def broken_local_links(site: Path, page: str) -> list[str]:
    """Relative hrefs on a built page that resolve to nothing in the site."""
    broken = []
    for href in _HREF.findall((site / page).read_text()):
        if href.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = href.split("#", 1)[0]
        target = site / posixpath.dirname(page) / path
        if not (target.is_file() or (target / "index.html").is_file()):
            broken.append(href)
    return broken


# ------------------------------------------------------------------- assembly


def tracked_files(root: Path) -> set[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
    ).stdout
    return set(out.split())


def head_commit(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "--short=7", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def main() -> int:
    target = ROOT / "dbt" / "target"
    manifest_path = target / "manifest.json"
    if not manifest_path.exists():
        print("no dbt manifest -- run `make dbt-docs` first", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text())
    repo_files = tracked_files(ROOT)
    adrs = load_adrs(ROOT, added_dates(ROOT))

    pages = stage_docs(ROOT, BUILD, adrs, repo_files)
    zensical = Path(sys.executable).parent / "zensical"
    subprocess.run(
        [str(zensical), "build", "--strict", "-f", str(BUILD / "zensical.toml")], check=True
    )

    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(BUILD / "out", OUT / "docs")
    (OUT / "lineage").mkdir()
    for name in ("index.html", "manifest.json", "catalog.json"):
        shutil.copy(target / name, OUT / "lineage" / name)
    shutil.copytree(SITE_SRC / "landing" / "assets", OUT / "assets")

    stats = compute_stats(
        manifest,
        python_tests=count_python_tests(ROOT / "tests"),
        adrs=len(adrs),
        doc_pages=pages,
    )
    landing = render_landing(
        (SITE_SRC / "landing" / "index.html").read_text(),
        stats=stats,
        nodes=lineage_nodes(manifest),
        adrs=adrs,
        commit=head_commit(ROOT),
    )
    (OUT / "index.html").write_text(landing)

    broken = broken_local_links(OUT, "index.html")
    if broken:
        print("landing page links to pages that were not built:", file=sys.stderr)
        for href in broken:
            print(f"  {href}", file=sys.stderr)
        return 1
    print(f"site built: {OUT.relative_to(ROOT)}/ ({pages} doc pages, {len(adrs)} ADRs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
