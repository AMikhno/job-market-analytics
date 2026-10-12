"""Join hand-labelled verdicts back onto discovered roles and report what predicts them.

Answers three questions the raw hit rate cannot:
  1. Which title rule pulled each role in, and how does each rule score?
  2. Which query variant produced the roles worth applying to?
  3. Are the good ones at companies the list already had? -- the only question that
     decides whether search discovery is worth keeping, since a role at a company
     already on the list would have reached the digest without any of this.

Every grouping is derived from the data, never hardcoded: change the queries, the
variants or the title seed and this still reports on whatever is there.

Labels are a TSV: url<TAB>verdict<TAB>why. Verdict is free text; anything starting
`y` counts as yes, `m` as maybe, `n` as no. Blank `why` is allowed but wasteful --
the reasons are what produce findings.

    uv run python tools/probes/exa-discovery/probe_label_analysis.py
"""

from __future__ import annotations

import argparse
import collections
import csv
import pathlib
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[3]
DISCOVERY = ROOT / "config" / "discovery"


def norm(url: str) -> str:
    """Comparable form of a posting URL.

    Percent-decoded because a board name with a space arrives encoded from one
    place and decoded from another, and lowercased because Ashby refs are display
    names whose case is not stable.
    """
    return urllib.parse.unquote(url or "").strip().lower().rstrip("/")


def bucket(verdict: str) -> str:
    v = (verdict or "").strip().lower()
    return {"y": "yes", "m": "maybe", "n": "no"}.get(v[:1], "?") if v else "?"


def load_labels(path: pathlib.Path) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    with open(path, newline="") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if not parts or not parts[0].strip() or parts[0].lstrip().startswith("#"):
                continue
            url = norm(parts[0])
            out[url] = (bucket(parts[1] if len(parts) > 1 else ""),
                        parts[2].strip() if len(parts) > 2 else "")
    return out


def table(title: str, agg: dict[str, collections.Counter], min_n: int = 1) -> None:
    print(f"\n--- {title} ---")
    print(f"  {'group':<28}{'n':>4}{'yes':>5}{'maybe':>7}{'no':>4}{'yes+maybe':>11}")
    for key, c in sorted(agg.items(), key=lambda kv: -sum(kv[1].values())):
        n = sum(c.values())
        if n < min_n:
            continue
        rate = (c["yes"] + c["maybe"]) / n
        print(f"  {key[:27]:<28}{n:>4}{c['yes']:>5}{c['maybe']:>7}{c['no']:>4}{rate:>10.0%}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=str(DISCOVERY / "labels_exa.tsv"))
    ap.add_argument("--roles", default=str(DISCOVERY / "exa_pilot_roles.csv"))
    ap.add_argument("--companies", default=str(ROOT / "config" / "companies.csv"))
    args = ap.parse_args()

    lpath, rpath = pathlib.Path(args.labels), pathlib.Path(args.roles)
    if not lpath.exists() or not rpath.exists():
        print(f"need both {lpath} and {rpath}", file=sys.stderr)
        return 2

    labels = load_labels(lpath)
    with open(rpath, newline="") as f:
        roles = {norm(r["url"]): r for r in csv.DictReader(f)}

    joined = [(v, why, roles[u]) for u, (v, why) in labels.items() if u in roles]
    missing = [u for u in labels if u not in roles]
    print(f"labels {len(labels)} | roles {len(roles)} | joined {len(joined)}")
    if missing:
        print(f"WARNING: {len(missing)} labelled URL(s) not found in the roles file "
              f"-- re-run probe_exa_roles.py, or the posting has since closed")
        for u in missing[:5]:
            print(f"    {u[:96]}")

    if not joined:
        return 1
    n = len(joined)
    v = collections.Counter(lab for lab, _, _ in joined)
    print(f"\nVERDICT  yes {v['yes']} ({v['yes']/n:.0%})   maybe {v['maybe']} "
          f"({v['maybe']/n:.0%})   no {v['no']} ({v['no']/n:.0%})"
          + (f"   unlabelled {v['?']}" if v["?"] else ""))
    print(f"         yes+maybe {(v['yes']+v['maybe'])/n:.0%}")

    by_title: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    by_query: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    by_src: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    by_mgmt: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    by_board: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for lab, _why, r in joined:
        for m in filter(None, r.get("title_match", "").split("|")):
            by_title[m][lab] += 1
        for q in filter(None, r.get("found_by", "").split("|")):
            by_query[q][lab] += 1
        by_src[r["source"]][lab] += 1
        by_mgmt["management-titled" if r.get("mgmt") else "individual contributor"][lab] += 1
        by_board[f"{r['source']}/{r['board_ref']}"][lab] += 1

    table("by title rule (which seed pattern pulled it in)", by_title)
    table("by query variant", by_query)
    table("by source", by_src)
    table("by seniority flag", by_mgmt)
    table("by board (2+ labelled roles)", by_board, min_n=2)

    # The decisive question: a good role at a company already on the list would
    # have reached the digest anyway. Only the ones at absent companies are what
    # search discovery actually bought.
    cpath = pathlib.Path(args.companies)
    if cpath.exists():
        with open(cpath, newline="") as f:
            known = {(r.get("source", "").strip().lower(),
                      (r.get("board_ref") or "").strip().lower())
                     for r in csv.DictReader(f)}
        new: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        for lab, _why, r in joined:
            pair = (r["source"].strip().lower(), r["board_ref"].strip().lower())
            new["already on the list" if pair in known else "NEW company"][lab] += 1
        print("\n--- roles at companies already on the list vs newly discovered ---")
        for key in ("NEW company", "already on the list"):
            c = new.get(key)
            if not c:
                continue
            tot = sum(c.values())
            print(f"  {key:<24}{tot:>4} roles   yes={c['yes']} maybe={c['maybe']} no={c['no']}")

    print("\n--- reasons given for 'no' ---")
    for lab, why, r in joined:
        if lab == "no" and why:
            print(f"  {r['title'][:42]:<44}{why[:46]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
