"""Workday probe, take 3 -- two questions.

(A) Is the 422 a *wrong-ref* signal or a *wrong-request* signal? Take 2 got 422
    for every tenant x site combination, including shards that cannot be right.
    If a known-good public board also 422s, my request shape is wrong and the
    survey's "endpoint is live" reading stands. If it returns 200, then 422 is
    the uniform reply to any unknown ref -- meaning the search space gives no
    feedback and brute force cannot work.

(B) Can the real board URL be recovered from the company website? That is the
    ref-schema pass the survey said Workday needs before any code. Measuring its
    hit rate is the actual difficulty estimate.
"""

from __future__ import annotations

import csv
import json
import re
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

# Public boards whose full tenant/shard/site is documented on the open web.
KNOWN_GOOD = [
    ("nvidia", 5, "NVIDIAExternalCareerSite"),
    ("salesforce", 12, "External_Career_Site"),
    ("workday", 5, "Workday"),
    ("cushwake", 3, "Cushman_Wakefield_Careers"),
]

BOARD_RE = re.compile(
    r"https?://([A-Za-z0-9\-]+)\.(wd\d+)\.myworkdayjobs\.com/"
    r"(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_\-]+)"
)
CAREER_HINT = re.compile(r"(career|job|opportunit|join|work-with|workhere)", re.I)


def cxs(tenant: str, shard: str, site: str) -> tuple[int, int, int | None]:
    url = f"https://{tenant}.{shard}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    body = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
    try:
        resp = requests.post(
            url,
            headers={**UA, "Content-Type": "application/json", "Accept": "application/json"},
            data=json.dumps(body),
            timeout=25,
        )
    except Exception as exc:
        print(f"      ERR {type(exc).__name__}")
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


def part_a() -> None:
    print("(A) known-good public boards — does the request shape work at all?\n")
    for tenant, shard, site in KNOWN_GOOD:
        time.sleep(0.5)
        status, n, total = cxs(tenant, f"wd{shard}", site)
        flag = "  <== JOBS" if n > 0 else ""
        print(f"    {tenant:<12} wd{shard}/{site:<28} {status} n={n} total={total}{flag}")


def find_board_from_site(website: str) -> list[str]:
    """Fetch the site, then its careers-ish links, hunting a myworkdayjobs URL."""
    if not website.startswith("http"):
        website = "https://" + website
    found: list[str] = []
    try:
        resp = requests.get(website, headers=UA, timeout=20, allow_redirects=True)
    except Exception:
        return []
    for m in BOARD_RE.findall(resp.text):
        s = "/".join(m)
        if s not in found:
            found.append(s)
    if found:
        return found

    # Follow up to 3 careers-looking links one hop deep.
    links = re.findall(r'href=["\']([^"\']+)["\']', resp.text)
    hops = []
    for href in links:
        if CAREER_HINT.search(href) and len(hops) < 3:
            if href.startswith("/"):
                href = resp.url.rstrip("/").split("/")[0] + "//" + resp.url.split("/")[2] + href
            if href.startswith("http") and href not in hops:
                hops.append(href)
    for href in hops:
        time.sleep(0.4)
        try:
            r2 = requests.get(href, headers=UA, timeout=20, allow_redirects=True)
        except Exception:
            continue
        for m in BOARD_RE.findall(r2.text) + BOARD_RE.findall(r2.url):
            s = "/".join(m)
            if s not in found:
                found.append(s)
        if found:
            break
    return found


def part_b(limit: int) -> None:
    print(f"\n\n(B) recovering the board URL from the company website (first {limit})\n")
    rows = [r for r in csv.DictReader(open("config/companies.csv")) if r["source"] == "workday"]
    hits = 0
    for row in rows[:limit]:
        name, site = row["company_name"], (row["website"] or "").strip()
        if not site:
            print(f"    {name[:24]:<26} no website stored")
            continue
        time.sleep(0.4)
        found = find_board_from_site(site)
        if found:
            hits += 1
            print(f"    {name[:24]:<26} FOUND {found[0]}")
        else:
            print(f"    {name[:24]:<26} —")
    print(f"\n    recovered {hits}/{min(limit, len(rows))} from the website alone")


if __name__ == "__main__":
    part_a()
    part_b(int(sys.argv[1]) if len(sys.argv) > 1 else 10)
