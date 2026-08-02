"""Probe candidate public endpoints for every ATS in the inventory.

Answers one question per ATS with evidence rather than recall: is there a
public, keyless, JSON feed reachable with the board_ref we already store? That
is the ADR-0013 bar for a V1 adapter.

Written because several ATS whose APIs are widely *described* as public turned
out to be authenticated (SuccessFactors: 401), key-gated (Teamtailor), or simply
gone (Workable's documented v3 path 404s while the v1 widget works). Recall is
not evidence here; a live 200 with parsed job records is.

    uv run --with requests --no-project python tools/company_discovery/ats_feed_probe.py

Read-only, no credentials, reads refs from the private list. Results as of
2026-07-28 are written up in docs/research/ats-feeds.md -- re-run this before
acting on that file rather than trusting its age.
"""

from __future__ import annotations

import csv
import json
import pathlib
import sys
import time
from collections import defaultdict

import requests

ROOT = pathlib.Path(__file__).resolve().parents[2]
MASTER = ROOT / "config" / "companies.csv"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}

# ats -> list of (label, method, url_template, json_body)
CANDIDATES: dict[str, list[tuple[str, str, str, dict | None]]] = {
    "workable": [("widget v1", "GET", "https://apply.workable.com/api/v1/widget/accounts/{r}?details=true", None),
                 ("published", "GET", "https://apply.workable.com/api/v3/accounts/{r}/jobs", None)],
    "recruitee": [("offers", "GET", "https://{r}.recruitee.com/api/offers/", None)],
    "smartrecruiters": [("postings", "GET", "https://api.smartrecruiters.com/v1/companies/{r}/postings?limit=100", None)],
    "bamboohr": [("careers list", "GET", "https://{r}.bamboohr.com/careers/list", None)],
    "breezyhr": [("json", "GET", "https://{r}.breezy.hr/json", None)],
    "pinpoint": [("postings", "GET", "https://{r}.pinpointhq.com/postings.json", None)],
    "teamtailor": [("jobs json", "GET", "https://{r}.teamtailor.com/jobs.json", None)],
    "jazzhr": [("feed", "GET", "https://{r}.applytojob.com/apply/jobs.json", None)],
    "eightfold": [("apply v2", "GET", "https://{r}.eightfold.ai/api/apply/v2/jobs?domain={r}.com&start=0&num=10", None)],
    "rippling": [("board api", "GET", "https://api.rippling.com/platform/api/ats/v1/board/{r}/jobs", None)],
    "jobvite": [("search json", "GET", "https://jobs.jobvite.com/api/v1/company/{r}/jobs", None)],
    "icims": [("search", "GET", "https://{r}.icims.com/jobs/search?ss=1&searchRelation=keyword_all&in_iframe=1", None)],
    "paylocity": [("api", "GET", "https://recruiting.paylocity.com/recruiting/v2/api/jobs/{r}", None)],
    "ukg": [("opportunities", "GET", "https://{r}.ultipro.com/api/opportunities", None)],
    "dayforce": [("api", "GET", "https://{r}.dayforcehcm.com/CandidatePortal/api/v1/Jobs", None)],
    "adp": [("api", "GET", "https://{r}.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions", None)],
    "successfactors": [("odata", "GET", "https://{r}.successfactors.com/odata/v2/JobRequisition", None)],
    "oraclehcm": [("recruiting CE", "GET", "https://{r}.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions", None)],
    "phenom": [("widget", "GET", "https://{r}.phenompeople.com/services/recruiting/jobs", None)],
    "workday": [("cxs POST", "POST", "https://{r}.wd1.myworkdayjobs.com/wday/cxs/{r}/External/jobs",
                 {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""})],
}

# Extra refs to probe beyond what the master list yields, as {ats: [ref, ...]}.
# Kept in a gitignored file rather than inline: this repo is public, and which
# companies are targeted is exactly what the company list is private to protect.
# Absent file -> master refs only, which is the normal case.
EXTRA_REFS_PATH = ROOT / "config" / "probe_extra_refs.json"


def extra_refs() -> dict[str, list[str]]:
    if not EXTRA_REFS_PATH.exists():
        return {}
    return json.loads(EXTRA_REFS_PATH.read_text())


def refs_from_master() -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for row in csv.DictReader(open(MASTER, newline="", encoding="utf-8")):
        ref = (row["board_ref"] or "").strip()
        if ref and ref not in out[row["source"]]:
            out[row["source"]].append(ref)
    return out


def looks_like_jobs(payload: object) -> int:
    """Rough count of job-ish records in a parsed JSON body."""
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("jobs", "offers", "content", "data", "positions", "results",
                    "jobPostings", "requisitionList", "items"):
            val = payload.get(key)
            if isinstance(val, list):
                return len(val)
        if payload.get("total") or payload.get("totalCount"):
            return int(payload.get("total") or payload.get("totalCount") or 0)
    return 0


def main() -> int:
    master_refs = refs_from_master()
    extra = extra_refs()
    results: dict[str, list[str]] = {}

    for ats, candidates in CANDIDATES.items():
        refs = (master_refs.get(ats, []) + extra.get(ats, []))[:4]
        refs = [r for r in refs if r]
        if not refs:
            results[ats] = ["no usable ref stored"]
            continue
        lines: list[str] = []
        for label, method, tmpl, body in candidates:
            for ref in refs:
                url = tmpl.replace("{r}", ref)
                try:
                    time.sleep(0.3)
                    if method == "POST":
                        resp = requests.post(url, headers={**UA, "Content-Type": "application/json"},
                                             data=json.dumps(body), timeout=20)
                    else:
                        resp = requests.get(url, headers=UA, timeout=20)
                except Exception as exc:
                    lines.append(f"    {label:14} {ref:24} ERR {type(exc).__name__}")
                    continue
                ctype = resp.headers.get("content-type", "")[:40]
                n = 0
                if resp.status_code == 200 and "json" in ctype:
                    try:
                        n = looks_like_jobs(resp.json())
                    except Exception:
                        n = -1
                flag = "  <== JSON JOBS" if n > 0 else ""
                lines.append(f"    {label:14} {ref:24} {resp.status_code} {ctype:34} n={n}{flag}")
                if n > 0:
                    break
        results[ats] = lines

    for ats in sorted(results):
        print(f"\n{ats}:")
        for line in results[ats]:
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
