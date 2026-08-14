"""Dump the stored ref/website for the unbuilt sources that matter for planning."""

import csv
import sys

WANT = set(sys.argv[1:]) or {"workday"}

rows = [r for r in csv.DictReader(open("config/companies.csv")) if r["source"] in WANT]
for r in sorted(rows, key=lambda r: (r["source"], r["company_name"])):
    print(
        f'{r["source"]:<16}{r["company_name"][:26]:<28}'
        f'ref={(r["board_ref"] or "-")[:34]:<36}'
        f'site={(r["website"] or "-")[:40]:<42}'
        f'notes={(r["notes"] or "")[:40]}'
    )
print(f"\n{len(rows)} rows")
