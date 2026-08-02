"""Fail on documentation that points at something which does not exist.

Prose drifts silently: a file gets renamed, a make target disappears, an ADR is
superseded, and the sentence referring to it keeps reading fine. Nothing in the
test suite notices, because nothing imports a paragraph.

This checks the references that are mechanically verifiable, and only those --
a noisy checker gets switched off, so precision matters more than coverage:

  * relative markdown links, and backticked repo paths (`ingest/pipeline.py`);
  * line-anchored references (`shared/config.py:56`) -- file exists *and* is
    long enough;
  * `ADR-0021` -> docs/decisions/0021-*.md exists;
  * `make <target>` -> the target exists in the Makefile;
  * dbt model names (`int_jobs__unioned`) -> a matching .sql exists;
  * heading anchors, in-file and cross-file.

Scans markdown *and* Python, because a stale path in a docstring is the same
defect as a stale path in a doc.

Lives in scripts/ rather than tools/ deliberately: tools/ is fenced off from the
repo's gates as manual tooling, and this is a gate.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOP_LEVEL_DIRS = {
    "ingest",
    "shared",
    "deliver",
    "dbt",
    "docs",
    "tests",
    "tools",
    "config",
    "scripts",
}
KNOWN_SUFFIXES = {
    ".py",
    ".md",
    ".sql",
    ".yml",
    ".yaml",
    ".csv",
    ".toml",
    ".json",
    ".ipynb",
    ".lock",
}


@dataclass(frozen=True)
class Problem:
    file: str
    line: int
    ref: str
    why: str


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    return [f for f in out if not f.startswith("dbt/dbt_packages/")]


def _make_targets() -> set[str]:
    text = (ROOT / "Makefile").read_text()
    return set(re.findall(r"^([a-zA-Z][\w-]*):", text, re.M))


def _dbt_models() -> set[str]:
    return {p.stem for p in (ROOT / "dbt" / "models").rglob("*.sql")}


def _allowed() -> set[str]:
    """Refs that are planned or gitignored -- see scripts/planned-refs.txt."""
    path = ROOT / "scripts" / "planned-refs.txt"
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }


def _adrs() -> set[str]:
    return {p.name[:4] for p in (ROOT / "docs" / "decisions").glob("*.md")}


def facts() -> dict[str, int]:
    """Counts computed from the code, for docs to assert against.

    Prose gets a number wrong the moment the code moves under it, and no
    reference check notices -- "nine sources" reads fine when there are ten.
    A doc opts in by tagging the sentence:

        The pipeline ingests **9** sources. <!-- check:sources -->

    The checker then requires that line to contain the computed value. Opt-in,
    because guessing which numbers in prose are claims about the code produces
    exactly the noise that gets a checker disabled.
    """
    sys.path.insert(0, str(ROOT))
    from ingest.sources import SOURCES
    from shared.storage import RAW_COLUMNS

    computed = {
        "sources": len(SOURCES),
        "active_sources": sum(1 for s in SOURCES if s.active),
        "raw_columns": len(RAW_COLUMNS),
        "adrs": len(_adrs()),
        "dbt_models": len(_dbt_models()),
    }
    # The company list is private and absent in CI, so this fact only exists
    # where the file does -- checked locally, skipped on the runner.
    companies = ROOT / "config" / "companies.csv"
    if companies.exists():
        import csv

        with companies.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
        computed["active_boards"] = sum(1 for r in rows if r.get("active", "").strip() == "true")
    return computed


def _fact_names(line: str) -> list[str]:
    """Fact names tagged on this line, e.g. `<!-- check:sources check:adrs -->`.

    Parsed as "names inside a comment" rather than one name per comment: an
    earlier version required exactly `<!-- check:name -->`, so a comment listing
    several names matched nothing and the check passed silently -- a green
    result that meant "found no claims", which is the worst thing a gate can do.
    """
    names: list[str] = []
    for comment in re.findall(r"<!--(.*?)-->", line, re.S):
        names += re.findall(r"check:(\w+)", comment)
    return names


def _check_facts(rel: str, known: dict[str, int]) -> list[Problem]:
    path = ROOT / rel
    problems: list[Problem] = []
    for i, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        for name in _fact_names(line):
            if name not in known:
                problems.append(Problem(rel, i, f"check:{name}", "no such computed fact"))
                continue
            value = str(known[name])
            if not re.search(rf"\b{value}\b", line):
                why = f"line does not state the real value ({value})"
                problems.append(Problem(rel, i, f"check:{name}", why))
    return problems


def _adapter_names() -> set[str]:
    sys.path.insert(0, str(ROOT))
    from ingest.sources import SOURCES

    return {s.adapter for s in SOURCES}


def _check_raw_tables(rel: str, adapters: set[str]) -> list[Problem]:
    """`raw_<source>_jobs` must name a source that is actually registered."""
    path = ROOT / rel
    problems: list[Problem] = []
    for i, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        for name in re.findall(r"\braw_(\w+?)_jobs\b", line):
            if name not in adapters:
                problems.append(
                    Problem(rel, i, f"raw_{name}_jobs", "no source registered with that name")
                )
    return problems


def _headings(path: Path) -> set[str]:
    """GitHub-style anchor slugs for a markdown file's headings."""
    slugs = set()
    for line in path.read_text(errors="replace").splitlines():
        m = re.match(r"^#{1,6}\s+(.*)", line)
        if not m:
            continue
        text = re.sub(r"`|\*|_", "", m.group(1)).strip().lower()
        slugs.add(re.sub(r"[^\w\s-]", "", text).replace(" ", "-"))
    return slugs


# Characters that mean the token is a regex, URL path or glob -- not a file.
_NOT_A_PATH = re.compile(r"[|\[\]^\\*?<>\"']")


def _looks_like_path(token: str) -> bool:
    if token.startswith(("http://", "https://", "#", "{", "/")) or " " in token:
        return False
    if _NOT_A_PATH.search(token):
        return False
    if re.search(r":\d+$", token):
        return False  # a line-anchored ref; _check_line_refs verifies those
    if "/" in token and token.split("/")[0] in TOP_LEVEL_DIRS:
        return True
    return Path(token).suffix in KNOWN_SUFFIXES and "/" in token


def _resolve(ref: str, *, source: Path) -> Path | None:
    """Resolve a reference to a real path, or None if it is not checkable.

    Docs address files three ways and all three are legitimate: from the repo
    root; from the referring file's own directory (a README naming its siblings
    without repeating the path to itself); and from the dbt project root, e.g.
    macros/cross_db.sql, because that is how dbt itself names them.
    """
    clean = ref.split("#")[0].rstrip(".,;:)")
    if not clean:
        return None
    # `module.py:Symbol` / `module.py::func` names a symbol, not a line; the
    # file is the part that can be verified.
    clean = re.sub(r"\.py:{1,2}[A-Za-z_]\w*$", ".py", clean)
    # `docs/decisions/0013` addresses an ADR by its number prefix.
    m = re.fullmatch(r"docs/decisions/(\d{4})", clean)
    if m:
        return next(iter((ROOT / "docs" / "decisions").glob(f"{m.group(1)}-*.md")), ROOT / clean)
    for base in (ROOT, source.parent, ROOT / "dbt"):
        if (base / clean).exists():
            return base / clean
    return ROOT / clean  # does not exist; report against the root form


def check_file(
    rel: str, *, targets: set[str], models: set[str], adrs: set[str], allowed: set[str]
) -> list[Problem]:
    path = ROOT / rel
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    problems: list[Problem] = []
    for i, line in enumerate(text.splitlines(), 1):
        for ref in _candidate_refs(line, markdown=rel.endswith(".md")):
            problem = _check_ref(
                rel, i, ref, targets=targets, models=models, adrs=adrs, source=path
            )
            if problem and problem.ref.split("#")[0].rstrip(".,;:)") not in allowed:
                problems.append(problem)
    return problems


def _candidate_refs(line: str, *, markdown: bool) -> list[str]:
    refs: list[str] = []
    if markdown:
        # Markdown-link syntax only in markdown: `](` also occurs inside Python
        # character classes (`["\']([^"\']+)["\']`), which are not links.
        refs += [
            f"LINK:{m}"
            for m in re.findall(r"\]\(([^)]+)\)", line)
            if not _NOT_A_PATH.search(m.split("#")[0])
        ]
    refs += [f"PATH:{m}" for m in re.findall(r"`([^`\s]+)`", line) if _looks_like_path(m)]
    # line-anchored refs (`file.py:56`) are handled by _check_line_refs, which
    # can also verify the line number rather than just the path
    refs += [f"ADR:{m}" for m in re.findall(r"\bADR-(\d{4})\b", line)]
    refs += [f"MAKE:{m}" for m in re.findall(r"`make ([a-zA-Z][\w-]*)", line)]
    refs += [f"DBT:{m}" for m in re.findall(r"`((?:stg|int|silver|fct|dim)_[\w]+)`", line)]
    return refs


def _check_ref(
    rel: str,
    line_no: int,
    tagged: str,
    *,
    targets: set[str],
    models: set[str],
    adrs: set[str],
    source: Path,
) -> Problem | None:
    kind, _, ref = tagged.partition(":")

    if kind == "ADR":
        return None if ref in adrs else Problem(rel, line_no, f"ADR-{ref}", "no such ADR")
    if kind == "MAKE":
        if ref in targets:
            return None
        return Problem(rel, line_no, f"make {ref}", "no such make target")
    if kind == "DBT":
        return None if ref in models else Problem(rel, line_no, ref, "no such dbt model")
    if kind in ("LINK", "PATH"):
        if ref.startswith(("http://", "https://", "mailto:")):
            return None
        if ref.startswith("#"):
            anchor = ref[1:]
            return (
                None
                if anchor in _headings(source)
                else Problem(rel, line_no, ref, "no such heading in this file")
            )
        target = _resolve(ref, source=source)
        if target is None:
            return None
        if not target.exists():
            return Problem(rel, line_no, ref, "path does not exist")
        if "#" in ref and target.suffix == ".md":
            anchor = ref.split("#", 1)[1]
            if anchor and anchor not in _headings(target):
                return Problem(rel, line_no, ref, "no such heading in the target file")
    return None


def _check_line_refs(rel: str) -> list[Problem]:
    """`path.py:NN` -- the file must exist and have at least NN lines."""
    path = ROOT / rel
    problems: list[Problem] = []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    for i, line in enumerate(text.splitlines(), 1):
        for file_ref, num in re.findall(r"\b([\w./-]+\.(?:py|sql|yml|md)):(\d+)\b", line):
            if "/" not in file_ref:
                continue  # a bare filename is ambiguous; skip rather than guess
            target = ROOT / file_ref
            if not target.exists():
                problems.append(Problem(rel, i, f"{file_ref}:{num}", "path does not exist"))
                continue
            total = len(target.read_text(errors="replace").splitlines())
            if int(num) > total:
                problems.append(
                    Problem(rel, i, f"{file_ref}:{num}", f"file has only {total} lines")
                )
    return problems


def main() -> int:
    targets, models, adrs, allowed = _make_targets(), _dbt_models(), _adrs(), _allowed()
    known, adapters = facts(), _adapter_names()
    problems: list[Problem] = []
    for rel in tracked_files():
        if not rel.endswith((".md", ".py")):
            continue
        problems += check_file(rel, targets=targets, models=models, adrs=adrs, allowed=allowed)
        problems += [p for p in _check_line_refs(rel) if p.ref.split(":")[0] not in allowed]
        if rel.endswith(".md"):
            problems += _check_facts(rel, known)
            problems += [p for p in _check_raw_tables(rel, adapters) if p.ref not in allowed]

    if not problems:
        print("docs check: all references resolve")
        return 0
    by_file: dict[str, list[Problem]] = {}
    for p in problems:
        by_file.setdefault(p.file, []).append(p)
    for file, items in sorted(by_file.items()):
        print(f"\n{file}")
        for p in sorted(items, key=lambda x: x.line):
            print(f"  line {p.line:>4}  {p.ref}  --  {p.why}")
    print(f"\ndocs check: {len(problems)} broken reference(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
