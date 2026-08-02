"""Probe: how discoverable is a Workday tenant's full board_ref (tenant/wdN/site)?

The stored list has tenant only. This asks, per tenant, whether the shard (wdN)
and the site segment can be found automatically -- because that, not the adapter
code, is what decides whether Workday is worth building.

Stage 1: find the shard by resolving {tenant}.wd{N}.myworkdayjobs.com.
Stage 2: find the site by following the shard root's redirect / scraping links.
Stage 3: confirm the cxs POST returns real job records for tenant+shard+site.

Read-only, keyless, paced. Public career boards only.
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
    )
}
SHARDS = [1, 2, 3, 5, 10, 12, 101, 103]


def _tenants_from_list() -> list[str]:
    """Workday refs from the private (gitignored) company list.

    Read rather than hardcoded: this repo is public, and which companies are
    targeted is exactly what the list is private to protect (CLAUDE.md).
    """
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

SITE_RE = re.compile(r"myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_\-]+)")


def find_shard(tenant: str) -> int | None:
    """DNS-resolve each shard host; only the tenant's real shard exists."""
    for n in SHARDS:
        host = f"{tenant}.wd{n}.myworkdayjobs.com"
        try:
            socket.gethostbyname(host)
            return n
        except socket.gaierror:
            continue
    return None


def find_sites(tenant: str, shard: int) -> list[str]:
    """The shard root redirects to (or links) the tenant's public site name."""
    url = f"https://{tenant}.wd{shard}.myworkdayjobs.com/"
    try:
        resp = requests.get(url, headers=UA, timeout=20, allow_redirects=True)
    except Exception as exc:
        print(f"    root GET  ERR {type(exc).__name__}")
        return []
    found: list[str] = []
    for cand in SITE_RE.findall(resp.url) + SITE_RE.findall(resp.text[:200_000]):
        if cand not in found and cand.lower() not in {"wday", "en-us"}:
            found.append(cand)
    print(f"    root GET  {resp.status_code} -> {resp.url[:78]}")
    return found[:4]


def try_cxs(tenant: str, shard: int, site: str) -> tuple[int, int]:
    """POST the cxs jobs endpoint. Returns (status, job count)."""
    url = f"https://{tenant}.wd{shard}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    body = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
    try:
        resp = requests.post(
            url,
            headers={**UA, "Content-Type": "application/json", "Accept": "application/json"},
            data=json.dumps(body),
            timeout=25,
        )
    except Exception as exc:
        print(f"    cxs {site:<26} ERR {type(exc).__name__}")
        return (0, 0)
    n = 0
    total = None
    if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
        try:
            payload = resp.json()
            n = len(payload.get("jobPostings") or [])
            total = payload.get("total")
        except Exception:
            n = -1
    flag = "  <== JOBS" if n > 0 else ""
    print(f"    cxs {site:<26} {resp.status_code} n={n} total={total}{flag}")
    return (resp.status_code, n)


def main() -> int:
    working: list[str] = []
    for tenant in TENANTS:
        print(f"\n{tenant}:")
        shard = find_shard(tenant)
        if shard is None:
            print("    no shard resolves — tenant name is wrong or board is private")
            continue
        print(f"    shard     wd{shard}")
        sites = find_sites(tenant, shard)
        if not sites:
            print("    no site name recoverable from the root")
            continue
        print(f"    sites     {sites}")
        for site in sites:
            time.sleep(0.5)
            status, n = try_cxs(tenant, shard, site)
            if n > 0:
                working.append(f"{tenant}/wd{shard}/{site}")
                break

    print(f"\n=== resolved {len(working)}/{len(TENANTS)} ===")
    for w in working:
        print("   ", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
