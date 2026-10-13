"""Turn reviewed discovery output into rows `make update-company-list` can take.

The unit is the board, not the posting that surfaced it: what enters the master is
a board the pipeline fetches daily, and a board carrying nothing relevant today can
carry something next month. So every reviewed board becomes a row unless something
about the *board* disqualifies it.

Writes the master's own schema, so the output drops straight into
`ingest/merge_companies.py`. Two columns need a human afterwards:

  company_name  derived from the ref when the board API carries no name. Marked in
                `notes` so it is findable, never silently wrong.
  website       blank. The probe only ever had posting URLs, and `website` is the
                recovery key that re-derives a ref after an ATS move (CLAUDE.md) --
                a wrong one is worse than an empty one. merge_companies fills blanks
                without overwriting, so a later audit closes it.

    uv run python tools/probes/exa-discovery/build_merge_candidates.py
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime
import importlib.util
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DISCOVERY = ROOT / "config" / "discovery"

_spec = importlib.util.spec_from_file_location("_a", HERE / "probe_label_analysis.py")
assert _spec and _spec.loader
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)

LIST_HEADERS = ["company_name", "source", "board_ref", "active", "tier", "website", "notes"]

# Boards that are real and live but must not be activated as-is. Matched against the
# ref and against the review text, because the two carry different evidence.
DISQUALIFY = {
    "internal": (
        re.compile(r"internal(?:s)?$|^internal", re.I),
        re.compile(r"internal (job )?board|internal only|current employees", re.I),
        "internal-only board; the external board is a different ref and needs a human",
    ),
}

# A board far larger than any single employer's is the portal/aggregator signature --
# the failure mode `tools/company_discovery/README.md` documents, arriving by a
# different route. Not auto-dropped: a staffing agency's board is also large and is a
# deliberate target (TODO.md priority 2), so this only asks a human to look.
HUGE_BOARD = 1000


def prettify(ref: str) -> str:
    """A readable name from a board ref, for the common case of no name in the API."""
    words = re.split(r"[-_.]+", ref.strip())
    return " ".join(w if w.isupper() else w.capitalize() for w in words if w)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roles", default=str(DISCOVERY / "exa_pilot_roles.csv"))
    ap.add_argument("--labels", default=str(DISCOVERY / "labels_exa_full.csv"))
    ap.add_argument("--master", default=str(ROOT / "config" / "companies.csv"))
    ap.add_argument("--out", default=str(DISCOVERY / "companies_from_search.csv"))
    ap.add_argument("--reviewed-only", action="store_true",
                    help="only boards carrying at least one reviewed posting")
    ap.add_argument("--verify", action="store_true",
                    help="re-check each board against its keyless API before emitting. "
                         "Worth it whenever the run is not fresh: a board that has gone "
                         "dead since should not be merged as active")
    args = ap.parse_args()

    verify = tier = None
    if args.verify:
        vspec = importlib.util.spec_from_file_location("_q", HERE / "probe_exa_queries.py")
        assert vspec and vspec.loader
        Q = importlib.util.module_from_spec(vspec)
        vspec.loader.exec_module(Q)
        allowed, non_viable = Q.load_allowed(), Q.load_non_viable()

        def verify(source: str, ref: str) -> tuple[int, str]:
            """Live postings and tier, retrying once on zero.

            A single zero is not evidence of a dead board: several of these APIs
            rate-limit a burst, and one board that answered with 3,512 postings on
            its own read back as empty inside a sweep. Since a zero here flips a
            company to inactive, the cheap second look is worth more than the time.
            """
            n, _ok, t, _top = Q.verify(source, ref, allowed, non_viable)
            if n == 0:
                time.sleep(3.0)
                n, _ok, t, _top = Q.verify(source, ref, allowed, non_viable, pause=2.0)
            return n, t

    with open(args.roles, newline="") as f:
        roles = list(csv.DictReader(f))
    labels = A.load_labels(pathlib.Path(args.labels))

    with open(args.master, newline="") as f:
        master = list(csv.DictReader(f))
    known = {(r["source"].strip().lower(), (r["board_ref"] or "").strip().lower())
             for r in master}

    # Board -> the reviews that landed on it, so a disqualifying note is visible
    # even when it was written against one posting.
    reviews: dict[tuple[str, str], list[tuple[str, str]]] = collections.defaultdict(list)
    for r in roles:
        key = (r["source"], r["board_ref"])
        hit = labels.get(A.norm(r["url"]))
        if hit:
            reviews[key].append(hit)

    today = datetime.date.today().isoformat()
    rows, skipped, flagged = [], [], []
    for key in sorted({(r["source"], r["board_ref"]) for r in roles}):
        source, ref = key
        if (source.lower(), ref.lower()) in known:
            skipped.append((source, ref, "already in the master"))
            continue
        seen = reviews.get(key, [])
        if args.reviewed_only and not seen:
            skipped.append((source, ref, "no reviewed posting"))
            continue

        blob = " ".join(why for _v, why in seen)
        note_bits = [f"search-discovered {today}"]
        active = "true"
        for name, (ref_pat, text_pat, reason) in DISQUALIFY.items():
            if ref_pat.search(ref) or text_pat.search(blob):
                active = "false"
                note_bits.append(reason)
                flagged.append((source, ref, name))
                break

        if verify is not None:
            live, now = verify(source, ref)
            if live == 0:
                active = "false"
                note_bits.append("board did not answer on re-check — confirm before activating")
                flagged.append((source, ref, "dead on re-check"))
            elif live >= HUGE_BOARD:
                active = "false"
                note_bits.append(f"{live} postings — confirm this is an employer, not a portal")
                flagged.append((source, ref, f"{live} postings"))
            else:
                note_bits.append(f"{live} postings, {now}, re-checked {today}")

        if not seen:
            note_bits.append("no posting reviewed")
        note_bits.append("website unknown — fill from a later audit")

        rows.append({
            "company_name": prettify(ref),
            "source": source,
            "board_ref": ref,
            "active": active,
            "tier": "1" if active == "true" else "2",
            "website": "",
            "notes": "; ".join(note_bits),
        })

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LIST_HEADERS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["active"] != "true", r["source"],
                                                r["board_ref"].lower())))

    act = sum(1 for r in rows if r["active"] == "true")
    print(f"{len(rows)} board(s) -> {args.out}")
    print(f"  {act} active, {len(rows) - act} held back for a human")
    print(f"  by source: {dict(collections.Counter(r['source'] for r in rows).most_common())}")
    if flagged:
        print("\n  HELD BACK:")
        for source, ref, why in flagged:
            print(f"    {source}/{ref}  — {why}")
    if skipped:
        reasons = collections.Counter(why for _s, _r, why in skipped)
        print(f"\n  skipped {len(skipped)}: {dict(reasons)}")
    print("\nEVERY company_name is derived from the ref and needs a human pass before "
          "merging.\nThen: make update-company-list INV=" + args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
