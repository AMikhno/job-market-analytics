"""Why were those careers pages 'empty' to a plain GET?

Two very different causes: the company genuinely has no openings, or the page is
client-rendered and requests.get() only saw the app shell. BeautifulSoup can
parse the first and is useless against the second, so the split decides whether
'just use bs4' is a viable plan.
"""

from __future__ import annotations

import csv
import re
import sys
import time

import requests

UA = "job-search-pipeline/0.1 (research probe; contact via repo)"
NAMES = {
    "Decisive Group Inc.", "Quantropi", "Ottawa General Contractors", "Design Centered Co.",
    "Tungsten Collaborative", "Goodwill Staffing", "Empress Effects", "Sinch", "Holitix",
    "Bank of Canada", "Edge Signal", "CENGN (Canada's Centre of Excellence in Next Generation Networks)",
}
SPA = re.compile(
    r'__NEXT_DATA__|__NUXT__|window\.__|id=["\'](root|app|__next)["\']|'
    r"ng-version|data-reactroot|wp-json|/_next/|vue\.js|react-dom",
    re.I,
)

rows = [
    r
    for r in csv.DictReader(open("/Users/Ana2026/Projects/job-search-pipeline/config/discovery/ats_audit_results.csv"))
    if r["Company Name"].strip() in NAMES and r["Career Page URL"].strip()
]
print(f"{'company':40} {'text':>7} {'scripts':>8} {'spa?':>5}", file=sys.stderr)
spa_like = plain = 0
for r in rows:
    try:
        html = requests.get(
            r["Career Page URL"].strip(), timeout=15, headers={"User-Agent": UA}
        ).text
    except Exception as exc:  # noqa: BLE001 - a probe: every outcome is data
        print(f"{r['Company Name'][:38]:40} {type(exc).__name__}")
        continue
    text = re.sub(r"\s+", " ", re.sub(r"(?s)<(script|style).*?</\1>|<[^>]+>", " ", html)).strip()
    scripts = len(re.findall(r"<script\b", html, re.I))
    spa = bool(SPA.search(html))
    # An app shell: lots of script, very little rendered prose.
    verdict = "SPA" if (spa and len(text) < 2500) else ("thin" if len(text) < 800 else "plain")
    spa_like += verdict == "SPA"
    plain += verdict == "plain"
    print(f"{r['Company Name'][:38]:40} {len(text):>7} {scripts:>8} {verdict:>5}")

print(f"\nlikely client-rendered: {spa_like} | served real text: {plain} | rest thin")
time.sleep(0)
