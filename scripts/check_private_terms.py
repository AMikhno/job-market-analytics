"""Block a commit that would put private identifiers into a public repo.

`gitleaks` catches credentials. This catches the other private thing this repo
has: **who is being searched for and by whom** — company names, board refs, and
whatever personal context has come up in conversation. Neither looks like a
secret, so nothing else was ever going to notice.

The terms come from files that are gitignored, which is the whole design:

  * `config/companies.csv` — company names and board refs, read automatically,
    because the list is already the authoritative set of company identifiers and
    a second hand-maintained copy would drift out of date;
  * `config/private-terms.txt` — one term per line, for anything else worth
    keeping out (a former employer, a topic word like `citizen` or `salary`).
    Optional; create it when you have something to add.

**This is a pre-commit hook and cannot be a CI gate.** CI has no company list,
so it cannot run the check — which is also why it must fail *open* when the
term sources are missing, rather than pass loudly and imply it checked.

Its real limitation, stated plainly: it matches **named** things. A paraphrase
("not eligible for most federal roles") contains no term and sails through. The
rule in CLAUDE.md is the control; this only catches the careless half.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPANIES = ROOT / "config" / "companies.csv"
EXTRA_TERMS = ROOT / "config" / "private-terms.txt"

# Company names and board refs that are also ordinary English or tech words.
# Matching these produces noise on every commit, and a noisy hook gets bypassed
# with --no-verify, which is the same as not having it.
SKIP = {
    "github",
    "render",
    "canonical",
    "shell",
    "campus",
    "redis",
    "users",
    "user",
    "access",
    "recruiting",
    "assets",
    "linkedin",
    "workforcenow",
    "careers",
    "career",
    "jobs",
    "job",
    "data",
    "cloud",
    "search",
    "team",
    "work",
    "people",
    "talent",
    "apply",
    "hiring",
    "main",
    "list",
    "app",
    "www",
    "harness",
    "motive",
    "solace",
}
MIN_LEN = 4


def _terms() -> dict[str, str]:
    """Private term -> what it is. Empty when no source file is present."""
    found: dict[str, str] = {}
    if COMPANIES.exists():
        with COMPANIES.open(newline="") as fh:
            for row in csv.DictReader(fh):
                fields = (
                    (row.get("company_name"), "company"),
                    (row.get("board_ref"), "board_ref"),
                )
                for value, kind in fields:
                    token = (value or "").strip()
                    if len(token) >= MIN_LEN and token.lower() not in SKIP:
                        found.setdefault(token.lower(), kind)
    if EXTRA_TERMS.exists():
        for line in EXTRA_TERMS.read_text().splitlines():
            token = line.split("#")[0].strip()
            if len(token) >= MIN_LEN and token.lower() not in SKIP:
                found.setdefault(token.lower(), "private term")
    return found


def scan(text: str, terms: dict[str, str]) -> list[tuple[int, str, str]]:
    """(line number, term, what it is) for every private term the text names."""
    hits: list[tuple[int, str, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        for term, kind in terms.items():
            if re.search(rf"\b{re.escape(term)}\b", low):
                hits.append((i, term, kind))
    return hits


def main(argv: list[str]) -> int:
    terms = _terms()
    if not terms:
        # Fail open, and say so: no company list means this is a clone or CI,
        # where the check cannot run. Silence here would look like a pass.
        print("private-terms: no term sources present; check skipped", file=sys.stderr)
        return 0

    problems: list[tuple[str, int, str, str]] = []
    for name in argv:
        path = Path(name)
        if path.resolve() in (COMPANIES.resolve(), EXTRA_TERMS.resolve()):
            continue  # the sources themselves are gitignored
        try:
            text = path.read_text(errors="replace")
        except OSError, UnicodeDecodeError:
            continue
        problems += [(name, i, t, k) for i, t, k in scan(text, terms)]

    if not problems:
        return 0
    print("\nPrivate identifiers must not be committed to a public repo:\n", file=sys.stderr)
    for name, line_no, term, kind in problems:
        print(f"  {name}:{line_no}  '{term}'  ({kind})", file=sys.stderr)
    print(
        "\nReplace it with the property it illustrates -- 'a two-word Ashby ref',"
        "\nnot the ref. See the documentation rules in CLAUDE.md."
        "\nIf the match is a false positive, add the word to SKIP in this script.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
