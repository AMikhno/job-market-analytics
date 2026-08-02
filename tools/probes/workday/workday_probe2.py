"""Workday probe, take 2.

Take 1 learned two things: *.myworkdayjobs.com looks like wildcard DNS (every
tenant "resolved" on wd1), and the root GET 406s without an Accept header. This
version verifies the wildcard, sends a browser-shaped Accept, and -- since the
site segment cannot be read off the root -- measures whether guessing it from a
common-name list actually works.
"""

from __future__ import annotations

import json
import re
import socket
import sys
import time

import requests

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
SHARDS = [1, 2, 3, 5, 10, 12, 101, 103]


def _tenants_from_list() -> list[str]:
    """Workday refs from the private (gitignored) company list -- see probe 1."""
    import csv
    from pathlib import Path

    path = Path("config/companies.csv")
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return [
            r["board_ref"].strip()
            for r in csv.DictReader(fh)
            if r["source"] == "workday" and r["board_ref"].strip()
        ]


TENANTS = sys.argv[1:] or _tenants_from_list()
if not TENANTS:
    sys.exit("no Workday tenants: pass them as arguments, or provide config/companies.csv")

# Site segments seen most often on public Workday boards.
COMMON_SITES = [
    "External",
    "Careers",
    "careers",
    "External_Careers",
    "ExternalCareers",
    "External_Career_Site",
    "en-US",
    "Search",
    "Jobs",
    "jobs",
]
SITE_RE = re.compile(r"myworkdayjobs\.com/(?:wday/[a-z]+/)?(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_\-]+)")


def wildcard_check() -> None:
    """If a nonsense tenant resolves, DNS tells us nothing about existence."""
    for host in (
        "definitely-not-a-real-tenant-xyz99.wd1.myworkdayjobs.com",
        "zzzznope.wd5.myworkdayjobs.com",
    ):
        try:
            print(f"  wildcard check {host} -> {socket.gethostbyname(host)}  (resolves!)")
        except socket.gaierror:
            print(f"  wildcard check {host} -> NXDOMAIN")


def root_probe(tenant: str, shard: int) -> tuple[int, list[str]]:
    url = f"https://{tenant}.wd{shard}.myworkdayjobs.com/"
    try:
        resp = requests.get(url, headers=UA, timeout=20, allow_redirects=True)
    except Exception as exc:
        return (0, [])
    sites: list[str] = []
    for cand in SITE_RE.findall(resp.url) + SITE_RE.findall(resp.text[:300_000]):
        if cand not in sites and cand.lower() not in {"wday", "cxs", "en-us", "app"}:
            sites.append(cand)
    return (resp.status_code, sites[:5])


def try_cxs(tenant: str, shard: int, site: str) -> tuple[int, int, int | None]:
    url = f"https://{tenant}.wd{shard}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    body = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
    try:
        resp = requests.post(
            url,
            headers={**UA, "Content-Type": "application/json", "Accept": "application/json"},
            data=json.dumps(body),
            timeout=25,
        )
    except Exception:
        return (0, 0, None)
    n, total = 0, None
    if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
        try:
            payload = resp.json()
            n = len(payload.get("jobPostings") or [])
            total = payload.get("total")
        except Exception:
            n = -1
    return (resp.status_code, n, total)


def main() -> int:
    print("DNS:")
    wildcard_check()

    resolved: list[str] = []
    for tenant in TENANTS:
        print(f"\n{tenant}:")
        hit = None
        for shard in SHARDS:
            status, sites = root_probe(tenant, shard)
            if status:
                print(f"    wd{shard:<4} root {status}  sites-from-html={sites}")
            if status == 200 and sites:
                hit = (shard, sites)
                break
        shard, sites = hit if hit else (1, [])
        candidates = list(dict.fromkeys(sites + COMMON_SITES))
        for site in candidates[:12]:
            time.sleep(0.4)
            status, n, total = try_cxs(tenant, shard, site)
            if n > 0:
                print(f"    cxs wd{shard}/{site:<22} {status} n={n} total={total}  <== JOBS")
                resolved.append(f"{tenant}/wd{shard}/{site} ({total} jobs)")
                break
            if status not in (404, 0):
                print(f"    cxs wd{shard}/{site:<22} {status} n={n}")
        else:
            print(f"    no working site among {len(candidates[:12])} candidates")

    print(f"\n=== resolved {len(resolved)}/{len(TENANTS)} ===")
    for r in resolved:
        print("   ", r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
