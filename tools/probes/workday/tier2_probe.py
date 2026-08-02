"""Close the survey's 'untested' gap: Jobvite, Oracle HCM, Paylocity, Eightfold.

The survey could not judge three of these because the list stores no board_ref.
This recovers a ref from each company's website (the same trick that worked for
Workday), then probes the candidate feed. Also re-confirms BreezyHR's missing
description, since that is the one Tier 1 platform still unbuilt.
"""

from __future__ import annotations

import csv
import json
import re
import time

import requests

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ats -> regex that pulls its ref out of any URL on the careers page
REF_PATTERNS = {
    "jobvite": re.compile(r"jobs\.jobvite\.com/([A-Za-z0-9\-_]+)"),
    "oraclehcm": re.compile(r"([A-Za-z0-9\-]+)\.(?:fa\.)?[a-z0-9]*\.?oraclecloud\.com"),
    "paylocity": re.compile(r"recruiting\.paylocity\.com/[Rr]ecruiting/[Jj]obs/[A-Za-z]+/([A-Za-z0-9\-]+)"),
    "eightfold": re.compile(r"([A-Za-z0-9\-]+)\.eightfold\.ai"),
    "teamtailor": re.compile(r"([A-Za-z0-9\-]+)\.teamtailor\.com"),
}
CAREER_HINT = re.compile(r"(career|job|opportunit|join|work)", re.I)


def recover_ref(website: str, pattern: re.Pattern[str]) -> str | None:
    if not website.startswith("http"):
        website = "https://" + website
    try:
        resp = requests.get(website, headers=UA, timeout=20, allow_redirects=True)
    except Exception:
        return None
    m = pattern.search(resp.text)
    if m:
        return m.group(1)
    hops = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', resp.text):
        if CAREER_HINT.search(href) and len(hops) < 3:
            if href.startswith("/"):
                href = "https://" + resp.url.split("/")[2] + href
            if href.startswith("http") and href not in hops:
                hops.append(href)
    for href in hops:
        time.sleep(0.3)
        try:
            r2 = requests.get(href, headers=UA, timeout=20, allow_redirects=True)
        except Exception:
            continue
        m = pattern.search(r2.text) or pattern.search(r2.url)
        if m:
            return m.group(1)
    return None


def probe_feed(ats: str, ref: str) -> str:
    urls = {
        "jobvite": f"https://jobs.jobvite.com/api/v1/company/{ref}/jobs",
        "oraclehcm": f"https://{ref}.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&limit=5",
        "paylocity": f"https://recruiting.paylocity.com/recruiting/v2/api/jobs/{ref}",
        "eightfold": f"https://{ref}.eightfold.ai/api/apply/v2/jobs?domain={ref}.com&start=0&num=10",
        "teamtailor": f"https://{ref}.teamtailor.com/jobs.json",
    }
    try:
        r = requests.get(urls[ats], headers={**UA, "Accept": "application/json"}, timeout=25)
    except Exception as exc:
        return f"ERR {type(exc).__name__}"
    ctype = r.headers.get("content-type", "")[:28]
    n = 0
    if r.status_code == 200 and "json" in ctype:
        try:
            p = r.json()
            for k in ("jobs", "requisitionList", "items", "positions", "data", "results"):
                if isinstance(p.get(k) if isinstance(p, dict) else None, list):
                    n = len(p[k])
                    break
            else:
                n = len(p) if isinstance(p, list) else 0
        except Exception:
            n = -1
    return f"{r.status_code} {ctype} n={n}" + ("  <== JOBS" if n > 0 else "")


def main() -> None:
    rows = list(csv.DictReader(open("config/companies.csv")))
    for ats, pattern in REF_PATTERNS.items():
        print(f"\n=== {ats} ===")
        for row in [r for r in rows if r["source"] == ats]:
            stored = (row["board_ref"] or "").strip()
            site = (row["website"] or "").strip()
            name = row["company_name"][:24]
            ref = stored or (recover_ref(site, pattern) if site else None)
            if not ref:
                print(f"    {name:<26} no ref (stored blank, not on website)")
                continue
            origin = "stored" if stored else "recovered"
            time.sleep(0.4)
            print(f"    {name:<26} {origin:<10} {ref[:22]:<24} {probe_feed(ats, ref)}")

    print("\n=== breezyhr — is there any keyless description? ===")
    for row in [r for r in rows if r["source"] == "breezyhr"]:
        ref = (row["board_ref"] or "").strip()
        if not ref:
            continue
        try:
            r = requests.get(f"https://{ref}.breezy.hr/json", headers=UA, timeout=20)
            jobs = r.json() if r.status_code == 200 else []
            keys = sorted(jobs[0]) if jobs else []
            has_desc = any("desc" in k.lower() for k in keys)
            print(f"    {ref:<16} list {r.status_code} n={len(jobs)} description_in_list={has_desc}")
            print(f"      keys: {keys}")
        except Exception as exc:
            print(f"    {ref:<16} ERR {type(exc).__name__}")


if __name__ == "__main__":
    main()
