"""Characterize a sample of the 'Unknown/Custom' careers pages.

Question: what shape are they actually in? Specifically -- does JobPosting
JSON-LD exist there (Ana suspects not), and if not, is the page structured
enough for a generic parser, or is it prose ending in "email us"?
"""

from __future__ import annotations

import csv
import json
import random
import re
import sys
import time

import requests

random.seed(11)
UA = "job-search-pipeline/0.1 (research probe; contact via repo)"
rows = [
    r
    for r in csv.DictReader(open("/Users/Ana2026/Projects/job-search-pipeline/config/discovery/ats_audit_results.csv"))
    if r["Detected ATS"].strip() == "Unknown/Custom" and r["Career Page URL"].strip()
]
sample = random.sample(rows, min(40, len(rows)))
print(f"{len(rows)} custom-careers companies; sampling {len(sample)}\n", file=sys.stderr)

JOBWORDS = re.compile(
    r"\b(engineer|analyst|manager|developer|specialist|coordinator|technician|"
    r"director|administrator|designer|accountant|supervisor|consultant)\b",
    re.I,
)
MAILTO = re.compile(r"mailto:|resume|resumé|cv\b|send (us )?your", re.I)

buckets = {"jsonld": [], "linky": [], "prose": [], "empty": [], "dead": []}
for r in sample:
    url, name = r["Career Page URL"].strip(), r["Company Name"].strip()
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": UA})
        if resp.status_code != 200:
            buckets["dead"].append((name, str(resp.status_code)))
            continue
        html = resp.text
    except Exception as exc:  # noqa: BLE001 - a probe: every outcome is data
        buckets["dead"].append((name, type(exc).__name__))
        continue

    has_jsonld = False
    for block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S | re.I
    ):
        if "jobposting" in block.lower():
            has_jsonld = True
            break
    # crude structure signal: anchors whose text looks like a job title
    anchors = re.findall(r"<a\b[^>]*>(.*?)</a>", html, re.S | re.I)
    titles = [re.sub(r"<[^>]+>", "", a).strip() for a in anchors]
    joblinks = [t for t in titles if 6 < len(t) < 90 and JOBWORDS.search(t)]
    text = re.sub(r"<[^>]+>", " ", html)
    mailto = bool(MAILTO.search(text)) or "mailto:" in html.lower()

    if has_jsonld:
        buckets["jsonld"].append((name, len(joblinks)))
    elif len(joblinks) >= 3:
        buckets["linky"].append((name, len(joblinks)))
    elif JOBWORDS.search(text) and mailto:
        buckets["prose"].append((name, len(joblinks)))
    else:
        buckets["empty"].append((name, len(joblinks)))
    time.sleep(0.4)

n = sum(len(v) for v in buckets.values())
print(json.dumps({k: len(v) for k, v in buckets.items()}, indent=2))
print(f"\nsampled {n}")
for k in ("jsonld", "linky", "prose", "empty", "dead"):
    if buckets[k]:
        print(f"\n--- {k} ---")
        for name, extra in buckets[k][:12]:
            print(f"  {name[:44]:46} {extra}")
