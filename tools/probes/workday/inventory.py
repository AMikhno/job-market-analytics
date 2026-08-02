"""Summarise the private company list by ATS: counts, active split, ref availability."""

import collections
import csv

BUILT = {
    "greenhouse",
    "lever",
    "ashby",
    "bamboohr",
    "recruitee",
    "workable",
    "pinpoint",
    "rippling",
    "smartrecruiters",
}

rows = list(csv.DictReader(open("config/companies.csv")))

tot = collections.Counter(r["source"] for r in rows)
act = collections.Counter((r["source"], r["active"].strip().lower()) for r in rows)
blank = collections.Counter(
    r["source"] for r in rows if not (r.get("board_ref") or "").strip()
)
site = collections.Counter(r["source"] for r in rows if (r.get("website") or "").strip())
tiers = collections.defaultdict(collections.Counter)
for r in rows:
    tiers[r["source"]][r.get("tier", "") or "-"] += 1

hdr = (
    f'{"source":<20}{"rows":>6}{"active":>8}{"inactive":>10}'
    f'{"blank_ref":>11}{"has_site":>10}  built'
)
print(hdr)
print("-" * len(hdr))
for src, n in tot.most_common():
    mark = "yes" if src in BUILT else ""
    print(
        f'{src:<20}{n:>6}{act[(src, "true")]:>8}{act[(src, "false")]:>10}'
        f"{blank[src]:>11}{site[src]:>10}  {mark}"
    )
print("-" * len(hdr))
active_total = sum(v for (s, a), v in act.items() if a == "true")
print(f"TOTAL rows {len(rows)}   active {active_total}   inactive {len(rows) - active_total}")

print("\n--- unbuilt sources, inactive rows, ref availability ---")
for src, n in tot.most_common():
    if src in BUILT:
        continue
    inactive = act[(src, "false")]
    with_ref = n - blank[src]
    print(
        f"{src:<20} inactive={inactive:<4} with_ref={with_ref:<4} "
        f"blank_ref={blank[src]:<4} with_website={site[src]}"
    )
