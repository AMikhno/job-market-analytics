"""Probe every active BambooHR board's list endpoint. List only -- enough to tell
a live board from a dead one, without the per-posting detail GETs."""

from __future__ import annotations

import sys

sys.path.insert(0, "/Users/Ana2026/Projects/job-search-pipeline")

from ingest.pipeline import load_companies  # noqa: E402
from ingest.sources import BambooHRSource  # noqa: E402
from shared.config import get_settings  # noqa: E402
from shared.http import SessionPool  # noqa: E402

settings = get_settings()
companies = load_companies("bamboohr", settings)
url = BambooHRSource(name="bamboohr").url_template
session = SessionPool(settings.http_user_agent).get()

live, empty, dead = [], [], []
for c in companies:
    try:
        r = session.get(url.format(board_ref=c.board_ref), timeout=20)
        if r.status_code != 200:
            dead.append((c.board_ref, str(r.status_code)))
            continue
        payload = r.json()
        n = len(payload.get("result", []) if isinstance(payload, dict) else payload)
        (live if n else empty).append((c.board_ref, n))
    except Exception as exc:  # noqa: BLE001 - a probe: every outcome is data
        dead.append((c.board_ref, type(exc).__name__))

print(f"active bamboohr boards: {len(companies)}")
print(f"  live (>0 postings): {len(live)}  total postings: {sum(n for _, n in live)}")
print(f"  live but empty:     {len(empty)}")
print(f"  failed:             {len(dead)}")
if empty:
    print("\nempty boards:", ", ".join(ref for ref, _ in empty))
if dead:
    print("\nfailed boards:")
    for ref, why in dead:
        print(f"  {ref}: {why}")
print("\ntop live boards:")
for ref, n in sorted(live, key=lambda x: -x[1])[:10]:
    print(f"  {ref}: {n}")
