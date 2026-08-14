"""Does Workday clear the ADR-0021 gate (a V1 source must yield a description)?

Checks what the cxs list carries per posting, then whether the detail endpoint
(/wday/cxs/{tenant}/{site}{externalPath}) supplies the description, public URL
and date -- i.e. whether Workday is a 1-call or a 1+N-call source, and how big
N is for a real board.
"""

from __future__ import annotations

import json
import sys
import time

import requests

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Content-Type": "application/json",
}
# tenant/shard/site triples to inspect. Default is a board whose full ref is
# documented on the open web; pass your own as "tenant shard site" arguments.
# Not hardcoded from the private list -- this repo is public.
BOARDS = [("nvidia", "wd5", "NVIDIAExternalCareerSite")]
if len(sys.argv) == 4:
    BOARDS = [(sys.argv[1], sys.argv[2], sys.argv[3])]


def show(tenant: str, shard: str, site: str) -> None:
    base = f"https://{tenant}.{shard}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
    body = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
    resp = requests.post(f"{base}/jobs", headers=UA, data=json.dumps(body), timeout=25)
    print(f"\n=== {tenant}/{shard}/{site} — list {resp.status_code}")
    if resp.status_code != 200:
        return
    payload = resp.json()
    posts = payload.get("jobPostings") or []
    print(f"    total={payload.get('total')}  returned={len(posts)}  top-keys={list(payload)}")
    if not posts:
        return
    first = posts[0]
    print(f"    posting keys: {sorted(first)}")
    print(f"    sample: {json.dumps(first, indent=6)[:700]}")

    path = first.get("externalPath")
    if not path:
        print("    no externalPath -> cannot build a detail URL")
        return
    time.sleep(0.5)
    d = requests.get(f"{base}{path}", headers=UA, timeout=25)
    print(f"    detail {d.status_code} {d.headers.get('content-type','')[:40]}")
    if d.status_code != 200 or "json" not in d.headers.get("content-type", ""):
        return
    info = (d.json() or {}).get("jobPostingInfo") or {}
    desc = info.get("jobDescription") or ""
    print(f"    detail keys: {sorted(info)[:18]}")
    print(f"    jobDescription: {len(desc)} chars -> {desc[:160]!r}")
    for k in ("externalUrl", "startDate", "postedOn", "timeType", "jobRequisitionId", "location"):
        print(f"      {k:18} {str(info.get(k))[:70]}")


if __name__ == "__main__":
    for b in BOARDS:
        try:
            show(*b)
        except Exception as exc:
            print(f"  {b} ERR {type(exc).__name__}: {exc}")
        time.sleep(0.6)
