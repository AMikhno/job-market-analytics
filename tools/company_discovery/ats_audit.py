#!/usr/bin/env python3
"""Reliable ATS audit: render each company's site, find its careers page, and read
the ATS it actually calls (network + DOM). Anchored to the company's own domain, so
no collisions; rendered, so JS-injected boards are visible.

Reads a source .xlsx (Company Name / Website) and writes results incrementally to CSV,
so a crash resumes instead of restarting.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import datetime
import os
import re
import threading

import openpyxl
import requests
import urllib3
import xml.etree.ElementTree as ET
from playwright.async_api import async_playwright

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}
OUT_HEADERS = ["Company Name", "Website", "Career Page URL", "Detected ATS",
               "Board Token", "Found Via", "Status"]
NAV_TIMEOUT = 25000  # ms

# ---------------------------------------------------------------- ATS host map
# host -> (ATS name, token capture). Order = priority; most specific first.
ATS_HOSTS = {
    "Greenhouse":      r"greenhouse\.io/(?:v1/boards/|embed/job_board(?:/js)?\?for=)?([\w.-]+)",
    "Lever":           r"(?:jobs|api)\.lever\.co/(?:v0/postings/)?([\w.-]+)",
    "Ashby":           r"ashbyhq\.com/(?:posting-api/job-board/)?([\w.-]+)",
    "SmartRecruiters": r"smartrecruiters\.com/(?:v1/companies/)?([\w.-]+)",
    "Workday":         r"([\w-]+)\.(?:wd\d+\.)?myworkdayjobs\.com",
    "BambooHR":        r"([\w-]+)\.bamboohr\.com",
    "Workable":        r"apply\.workable\.com/([\w.-]+)",
    "Recruitee":       r"([\w-]+)\.recruitee\.com",
    "Teamtailor":      r"([\w-]+)\.teamtailor\.com",
    "BreezyHR":        r"([\w-]+)\.breezy\.hr",
    "JazzHR":          r"([\w-]+)\.applytojob\.com",
    "Pinpoint":        r"([\w-]+)\.pinpointhq\.com",
    "iCIMS":           r"([\w-]+)\.icims\.com",
    "Dayforce":        r"([\w-]+)\.dayforcehcm\.com",
    "ADP":             r"(workforcenow|recruiting|myjobs)\.adp\.com",
    # smaller / enterprise-HRIS platforms found in the Ottawa list's Unknown bucket
    # (all inventory-only — none is a V1 keyless feed).
    "SuccessFactors":  r"(?:[\w-]+\.)?(?:successfactors\.(?:com|eu)|sapsf\.com)|jobs\.sap\.com",
    "UKG":             r"([\w-]+)\.(?:ukg|ultipro|ukgpro)\.(?:com|ca)",
    "Oracle HCM":      r"\.oraclecloud\.com|([\w-]+)\.taleo\.net",
    "Paylocity":       r"recruiting\.paylocity\.com",
    "Rippling":        r"ats\.rippling\.com",
    "Jobvite":         r"jobvite\.com",
    "Phenom":          r"([\w-]+)\.phenompeople\.com|phenom\.com",
    "Eightfold":       r"([\w-]+)\.eightfold\.ai",
    "Njoyn":           r"([\w-]+\.)?njoyn\.com",       # Canadian ATS (gov/enterprise)
    "Humi":            r"([\w-]+)\.humi\.ca",           # Canadian HRIS
    "Indeed":          r"(?:[\w-]+\.)?indeed\.com",     # aggregator link — keep last
}
_BAD_TOKENS = {"v1", "v0", "api", "embed", "jobs", "job", "boards", "board", "www",
               "posting-api", "job-board", "postings", "companies", "for", "js"}


def normalize_domain(raw: str) -> str:
    d = (raw or "").strip().lower()
    if not d:
        return ""
    if "://" in d:
        d = d.split("://", 1)[1]
    return d.split("/")[0]


def match_ats(url: str):
    u = (url or "").lower()
    for ats, pat in ATS_HOSTS.items():
        m = re.search(pat, u)
        if m:
            # first participating capture group (alternations may leave some None)
            tok = next((g for g in m.groups() if g), "") if m.groups() else ""
            return ats, tok
    return None


# ---------------------------------------------------------------- consent
CONSENT_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    "#CybotCookiebotDialogBodyButtonAccept",
    "#hs-eu-confirmation-button",
    "button[id*='accept' i]", "button[class*='accept' i]",
    "button[aria-label*='accept' i]",
    ".cc-allow", ".cookie-accept", ".js-accept-cookies",
]
CONSENT_TEXT = re.compile(
    r"^\s*(accept( all| cookies)?|allow all|i agree|agree|got it|ok(ay)?)\s*$", re.I)


async def dismiss_consent(page) -> bool:
    for sel in CONSENT_SELECTORS:
        try:
            await page.locator(sel).first.click(timeout=700)
            await page.wait_for_timeout(300)
            return True
        except Exception:
            continue
    for role in ("button", "link"):
        try:
            await page.get_by_role(role, name=CONSENT_TEXT).first.click(timeout=700)
            await page.wait_for_timeout(300)
            return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------- careers discovery
def _career_locs(xml_text: str):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return [], []
    locs = [e.text for e in root.iter() if e.tag.endswith("loc") and e.text]
    career = [u for u in locs if re.search(r"career|jobs?|join|hiring|opportunit", u, re.I)]
    nested = [u for u in locs if u.lower().endswith(".xml")]
    return career, nested


def careers_from_sitemap(domain: str):
    tried: set[str] = set()
    hits: list[str] = []
    seeds = [f"https://{domain}/sitemap.xml", f"https://{domain}/sitemap_index.xml"]
    try:
        rb = requests.get(f"https://{domain}/robots.txt", headers=HEADERS, timeout=6, verify=False)
        seeds += [l.split(":", 1)[1].strip() for l in rb.text.splitlines()
                  if l.lower().startswith("sitemap:")]
    except Exception:
        pass
    for sm in seeds:
        if sm in tried:
            continue
        tried.add(sm)
        try:
            r = requests.get(sm, headers=HEADERS, timeout=8, verify=False)
            if r.status_code >= 400:
                continue
        except Exception:
            continue
        career, nested = _career_locs(r.text)
        hits += career
        for sub in nested[:8]:
            if sub in tried:
                continue
            tried.add(sub)
            try:
                rr = requests.get(sub, headers=HEADERS, timeout=8, verify=False)
                hits += _career_locs(rr.text)[0]
            except Exception:
                pass
        if hits:
            break
    hits.sort(key=lambda u: bool(re.search(r"careers?/?$|jobs?/?$", u.lower())), reverse=True)
    return list(dict.fromkeys(hits))


def _first_path_ok(domain: str) -> str:
    for p in ("/careers", "/careers/", "/careers/search/", "/jobs", "/join-us"):
        try:
            r = requests.get(f"https://{domain}{p}", headers=HEADERS, timeout=6,
                             verify=False, allow_redirects=True)
            if r.status_code < 400:
                return r.url
        except Exception:
            pass
    return ""


async def find_careers(page, domain: str):
    href = await page.evaluate("""() => {
        const hint=/career|jobs?\\b|join.?us|hiring|opportunit|work.?with.?us/i;
        const a=[...document.querySelectorAll('a[href]')]
          .map(x=>({h:x.href,t:(x.textContent||'')}))
          .filter(x=>hint.test(x.t)||hint.test(x.h));
        a.sort((p,q)=>(/career|jobs?\\/?$/i.test(q.h)?1:0)-(/career|jobs?\\/?$/i.test(p.h)?1:0));
        return a.length?a[0].h:null; }""")
    if href:
        return href, "dom"
    sm = await asyncio.to_thread(careers_from_sitemap, domain)
    if sm:
        return sm[0], "sitemap"
    path = await asyncio.to_thread(_first_path_ok, domain)
    return (path, "path") if path else ("", "none")


# ---------------------------------------------------------------- audit one company
async def audit(ctx, name: str, website: str) -> dict:
    hits: list = []
    page = await ctx.new_page()

    def on_req(req):
        m = match_ats(req.url)
        if m:
            hits.append((m, req.url))

    page.on("request", on_req)

    domain = normalize_domain(website)
    if not domain:
        await page.close()
        return dict(name=name, website=website, career_url="", ats="N/A",
                    token="", via="-", status="no domain")

    try:
        await page.goto(f"https://{domain}", wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        await page.wait_for_timeout(1000)
        await dismiss_consent(page)
    except Exception:
        pass

    career_url, via = "", "none"
    try:
        career_url, via = await find_careers(page, domain)
    except Exception:
        pass

    if career_url:
        try:
            await page.goto(career_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            await dismiss_consent(page)
            await page.wait_for_timeout(2500)
        except Exception:
            pass

    try:
        srcs = await page.evaluate(
            "() => [...document.querySelectorAll('iframe[src],script[src],a[href]')]"
            ".map(e=>e.src||e.href)")
        for s in srcs:
            m = match_ats(s)
            if m:
                hits.append((m, s))
    except Exception:
        pass

    await page.close()
    if hits:
        (ats, token), url = hits[0]
        # guard against a path fragment sneaking through as a token
        if token.lower() in _BAD_TOKENS:
            token = ""
        return dict(name=name, website=website, career_url=career_url or url,
                    ats=ats, token=token, via=via, status="OK (rendered+network)")
    return dict(name=name, website=website, career_url=career_url,
                ats=("Unknown/Custom" if career_url else "N/A"), token="", via=via,
                status=("rendered, no ATS" if career_url else "no career page"))


# ---------------------------------------------------------------- I/O + runner
def load_companies(xlsx_path: str, sheet: str):
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb[sheet]
    out = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue  # header
        if row and row[0] and str(row[0]).strip():
            out.append({"name": str(row[0]).strip(),
                        "website": (str(row[1]).strip() if len(row) > 1 and row[1] else "")})
    return out


def load_done(csv_path: str) -> dict:
    done = {}
    if not os.path.exists(csv_path):
        return done
    with open(csv_path, newline="") as f:
        for r in csv.DictReader(f):
            key = normalize_domain(r.get("Website", "")) or r.get("Company Name", "").lower()
            if key and r.get("Status"):
                done[key] = [r.get(h, "") for h in OUT_HEADERS]
    return done


def write_csv(csv_path: str, records: list, lock: threading.Lock):
    with lock:
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(OUT_HEADERS)
            w.writerows(records)


async def run(companies, csv_path, concurrency, checkpoint_every):
    done = load_done(csv_path)
    records, todo = [], []
    for c in companies:
        key = normalize_domain(c["website"]) or c["name"].lower()
        if key in done:
            records.append(done[key])
        else:
            records.append([c["name"], c["website"], "", "", "", "", ""])
            todo.append((len(records) - 1, c))

    print(f"{len(companies)} companies | {len(done)} already audited | {len(todo)} to do",
          flush=True)
    if not todo:
        return records

    lock = threading.Lock()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=HEADERS["User-Agent"])
        sem = asyncio.Semaphore(concurrency)

        async def worker(i, c):
            async with sem:
                try:
                    return i, await audit(ctx, c["name"], c["website"])
                except Exception as e:
                    return i, dict(name=c["name"], website=c["website"], career_url="",
                                   ats="ERROR", token="", via="-", status=type(e).__name__)

        tasks = [asyncio.create_task(worker(i, c)) for i, c in todo]
        processed = 0
        for fut in asyncio.as_completed(tasks):
            i, res = await fut
            records[i] = [res["name"], res["website"], res["career_url"], res["ats"],
                          res["token"], res["via"], res["status"]]
            processed += 1
            if processed % checkpoint_every == 0:
                await asyncio.to_thread(write_csv, csv_path, records, lock)
                print(f"  ...{processed}/{len(todo)} audited (saved)  "
                      f"[{res['name']}: {res['ats']}]", flush=True)
        await browser.close()

    write_csv(csv_path, records, lock)
    print("Audit complete.", flush=True)
    return records


def emit_ingestable(records, out_path):
    _bare = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    src_map = {"Greenhouse": "greenhouse", "Lever": "lever", "Ashby": "ashby"}
    today = datetime.date.today().isoformat()
    seen, clean, review = set(), [], []
    for r in records:
        ats, token = r[3], (r[4] or "").strip()
        if ats not in src_map:
            continue
        source = src_map[ats]
        key = (source, token.lower())
        if not token or key in seen:
            continue
        seen.add(key)
        row = [r[0], source, token, "true", "1", f"auto-detected {today} (careers via {r[5]})"]
        (clean if _bare.fullmatch(token) and token.lower() not in _BAD_TOKENS else review).append(row)
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["company_name", "source", "board_ref", "active", "tier", "notes"])
        for row in sorted(clean, key=lambda x: (x[1], x[0].lower())):
            w.writerow(row)
    return clean, review


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True,
                    help="source .xlsx with Company Name / Website columns")
    ap.add_argument("--sheet", default="Company List")
    ap.add_argument("--out", default="ats_audit_results.csv",
                    help="full audit CSV (default: cwd)")
    ap.add_argument("--ingestable", default="companies_ingestable.csv",
                    help="GH/Lever/Ashby rows in the config/companies.csv schema (default: cwd)")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--checkpoint-every", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0, help="0 = all; else first N (for testing)")
    args = ap.parse_args()

    companies = load_companies(args.xlsx, args.sheet)
    if args.limit:
        companies = companies[:args.limit]

    records = asyncio.run(run(companies, args.out, args.concurrency, args.checkpoint_every))

    from collections import Counter
    print("\nATS:", dict(Counter(r[3] for r in records if r[3])))
    print("Found via:", dict(Counter(r[5] for r in records if r[5])))
    clean, review = emit_ingestable(records, args.ingestable)
    print(f"\nIngestable (GH/Lever/Ashby): {len(clean)} clean, {len(review)} need review")
    print(f"  full audit  -> {args.out}")
    print(f"  companies   -> {args.ingestable}")
    if review:
        print("  REVIEW (bad token capture):")
        for row in review:
            print(f"    {row[0]:26}{row[1]:11}board_ref={row[2]!r}")


if __name__ == "__main__":
    main()
