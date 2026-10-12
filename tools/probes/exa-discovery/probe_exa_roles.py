"""What roles are actually on the boards the search probe found?

`probe_exa_queries.py` answers "is this a real board, and does it post anywhere I
can work?" -- which is a location question, and location alone is the wrong filter.
A board that is 100% Canadian is useless if every posting is a lawyer or an account
executive; a board that is 5% Canadian is exactly right if that 5% is an analytics
role. One close match beats twenty near ones.

So this re-reads the candidate refs, fetches each board again, and keeps only the
postings that are BOTH in an allowed location AND carry a desired title. Output is
one row per matching *posting* -- the thing worth reviewing by hand before deciding
what to merge.

Costs nothing: every call is to a keyless ATS API. No Exa involved.

    uv run python tools/probes/exa-discovery/probe_exa_roles.py
"""

from __future__ import annotations

import argparse
import collections
import csv
import importlib.util
import pathlib
import re
import sys
import time

import requests

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DISCOVERY = ROOT / "config" / "discovery"

# The sibling probe holds the ATS knowledge (endpoints, payload shapes, the
# location walker). Loaded by path because "exa-discovery" is not an importable
# package name.
_spec = importlib.util.spec_from_file_location("_q", HERE / "probe_exa_queries.py")
assert _spec and _spec.loader
Q = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(Q)

UA = "job-market-analytics/0.1 (research probe; contact via repo)"

# Where each ATS keeps a posting's title and public URL. Tried in order; the first
# present key wins. Same tactic as the location walker -- several of these payload
# shapes are unverified, and a guessed field path fails silently.
TITLE_KEYS = ["title", "name", "text", "jobOpeningName", "position", "jobTitle"]
URL_KEYS = ["absolute_url", "hostedUrl", "jobUrl", "careers_url", "applyUrl",
            "url", "shortlink", "applyLink", "link"]


# People-management titles. Flagged, never dropped -- the call is the reviewer's,
# and "Manager" in a RevOps or analytics title is sometimes an IC role. Lead,
# Staff and Principal are deliberately absent: those are IC band, not management.
MGMT = re.compile(r"\b(manager|director|head of|vp|vice president|chief)\b", re.I)


def _first(job: dict, keys: list[str]) -> str:
    for k in keys:
        v = job.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def load_desired_titles() -> list[tuple[str, re.Pattern[str]]]:
    """(pattern text, compiled) from the same seed the warehouse ranks on."""
    seed = ROOT / "dbt" / "seeds" / "desired_titles.csv"
    if not seed.exists():
        return []
    out = []
    with open(seed, newline="") as f:
        for r in csv.DictReader(f):
            p = (r.get("pattern") or "").strip()
            if p:
                out.append((p, re.compile(rf"\b{re.escape(p)}", re.I)))
    return out


def fetch_board(source: str, ref: str, pause: float = 0.3) -> list[dict]:
    tmpl, key = Q.VERIFY_APIS[source]
    try:
        time.sleep(pause)
        resp = requests.get(tmpl.format(t=ref), headers={"User-Agent": UA}, timeout=25)
        if resp.status_code != 200:
            return []
        payload = resp.json()
    except Exception:  # noqa: BLE001 - a probe: every outcome is data
        return []
    jobs = payload if key is None else (payload.get(key) if isinstance(payload, dict) else None)
    return [j for j in jobs if isinstance(j, dict)] if isinstance(jobs, list) else []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default=str(DISCOVERY / "exa_pilot_candidates.csv"))
    ap.add_argument("--out", default=str(DISCOVERY / "exa_pilot_roles.csv"))
    ap.add_argument("--include-excluded", action="store_true",
                    help="also re-read boards tiered `excluded` (a remote-Canada role "
                         "can sit on an otherwise non-viable board)")
    args = ap.parse_args()

    cpath = pathlib.Path(args.candidates)
    if not cpath.exists():
        print(f"no candidates at {cpath} -- run probe_exa_queries.py first", file=sys.stderr)
        return 2
    with open(cpath, newline="") as f:
        cands = list(csv.DictReader(f))
    if not args.include_excluded:
        cands = [c for c in cands if c["tier"] != "excluded"]

    allowed = Q.load_allowed()
    titles = load_desired_titles()
    if not allowed or not titles:
        print("missing dbt/seeds/allowed_locations.csv or desired_titles.csv "
              "-- both are required here", file=sys.stderr)
        return 2
    print(f"{len(cands)} boards to re-read | {len(allowed)} location patterns | "
          f"{len(titles)} title patterns\n")

    rows: list[dict] = []
    boards_with_hits = 0
    for i, c in enumerate(cands, start=1):
        jobs = fetch_board(c["source"], c["board_ref"])
        hits = []
        for j in jobs:
            loc = Q._location_text(j).strip()
            if not any(p.search(loc) for p in allowed):
                continue
            title = _first(j, TITLE_KEYS)
            matched = [t for t, pat in titles if pat.search(title)]
            if not matched:
                continue
            hits.append({
                "board_ref": c["board_ref"], "source": c["source"], "tier": c["tier"],
                "title": title, "location": loc[:80],
                "mgmt": "yes" if MGMT.search(title) else "",
                "title_match": "|".join(matched[:3]),
                "board_postings": c["live_postings"],
                "url": _first(j, URL_KEYS), "found_by": c["found_by"],
            })
        if hits:
            boards_with_hits += 1
            rows.extend(hits)
        if i % 40 == 0:
            print(f"  ...{i}/{len(cands)} boards read, {len(rows)} matching roles "
                  f"on {boards_with_hits} boards", flush=True)

    # IC roles first within each board -- the management ones are the likely skips.
    rows.sort(key=lambda r: (r["source"], r["board_ref"].lower(), r["mgmt"], r["title"]))
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["board_ref", "source", "title", "location",
                                          "mgmt", "title_match", "tier",
                                          "board_postings", "found_by", "url"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in w.fieldnames})

    # Board-level view: merging happens per company, so this is the list to triage.
    per_board = collections.Counter(r["board_ref"] for r in rows)
    ic_board = collections.Counter(r["board_ref"] for r in rows if not r["mgmt"])
    seen: dict[str, dict] = {}
    for r in rows:
        seen.setdefault(r["board_ref"], r)
    bpath = pathlib.Path(args.out).with_name(
        pathlib.Path(args.out).stem + "_by_board.csv")
    with open(bpath, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["board_ref", "source", "ic_roles", "total_roles", "board_postings",
                    "found_by", "titles"])
        for ref, n in sorted(per_board.items(),
                             key=lambda kv: (-ic_board[kv[0]], -kv[1])):
            base = seen[ref]
            titles_here = [r["title"] for r in rows if r["board_ref"] == ref
                           and not r["mgmt"]][:4]
            w.writerow([ref, base["source"], ic_board[ref], n, base["board_postings"],
                        base["found_by"], " | ".join(titles_here)])

    print(f"\n{len(rows)} matching roles on {boards_with_hits} of {len(cands)} boards")
    print(f"  {sum(1 for r in rows if not r['mgmt'])} individual-contributor, "
          f"{sum(1 for r in rows if r['mgmt'])} management-titled")
    print(f"-> {args.out}")
    print(f"-> {bpath}\n")
    print(f"boards with exactly 1 matching role: "
          f"{sum(1 for v in per_board.values() if v == 1)}")
    print("most common title matches:")
    for t, n in collections.Counter(
            m for r in rows for m in r["title_match"].split("|")).most_common(12):
        print(f"  {n:>4}  {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
