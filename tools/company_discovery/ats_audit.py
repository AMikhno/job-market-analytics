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
import concurrent.futures
import csv
import datetime
import os
import re
import threading
import time
import urllib.parse

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
    # `.eu.` shard included: Lever hosts some boards on jobs.eu/api.eu.lever.co and
    # the plain host 404s for them, so without this they read as Unknown/Custom.
    "Lever":           r"(?:jobs|api)(?:\.eu)?\.lever\.co/(?:v0/postings/)?([\w.-]+)",
    # Token may contain spaces ("REDACTED"); URLs are unquoted before
    # matching (see match_ats), so allow spaces here and strip the edges after.
    "Ashby":           r"ashbyhq\.com/(?:posting-api/job-board/)?([\w.-]+(?: [\w.-]+)*)",
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

# board_ref format rules, mirroring ingest/sources.py. Ashby board names are
# display names and may carry single inner spaces (verified: the API accepts
# them, case-insensitively); Greenhouse/Lever stay bare tokens.
_BARE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ASHBY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?: [A-Za-z0-9._-]+)*$")


def ref_ok(source: str, token: str) -> bool:
    pattern = _ASHBY if source == "ashby" else _BARE
    return bool(pattern.fullmatch(token)) and token.lower() not in _BAD_TOKENS


def normalize_domain(raw: str) -> str:
    d = (raw or "").strip().lower()
    if not d:
        return ""
    if "://" in d:
        d = d.split("://", 1)[1]
    return d.split("/")[0]


def match_ats(url: str):
    # Percent-decode first: an encoded board name ("Dominion%20Dynamics") used to
    # truncate at the '%', silently yielding the wrong token ("dominion", which
    # 404s) instead of the real one.
    u = urllib.parse.unquote(url or "").lower()
    for ats, pat in ATS_HOSTS.items():
        m = re.search(pat, u)
        if m:
            # first participating capture group (alternations may leave some None)
            tok = next((g for g in m.groups() if g), "") if m.groups() else ""
            return ats, tok.strip()
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


async def settle(page, idle_ms: int = 4500) -> None:
    """Wait for the board to actually load, not a fixed guess.

    Most modern careers pages fetch their postings by XHR *after* first paint.
    A flat 2.5s wait closed the page before that request fired, so the network
    listener saw nothing and the company read as "no ATS" while sitting on one.
    Falls back to a short fixed wait when the page never goes idle (ad/analytics
    beacons keep some sites busy indefinitely).
    """
    try:
        await page.wait_for_load_state("networkidle", timeout=idle_ms)
    except Exception:
        await page.wait_for_timeout(2500)


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


# Links worth a second hop when the careers page itself shows no ATS. A landing
# page like /careers is often pure marketing, with the board one click further
# on ("Open roles", "See all jobs"); stopping at the first match made ~30 of 49
# unresolved companies read as "no ATS" when they were on one.
_DEEPER = re.compile(
    r"open.?(roles|positions)|current.?openings|all.?(jobs|roles|positions)"
    r"|view.?(jobs|roles|openings)|job.?(board|openings|search)|vacanc|apply",
    re.I,
)


async def deeper_board_links(page, domain: str) -> list[str]:
    """Candidate second-hop URLs, best first.

    Ranked so an off-site link to a known ATS host wins over an on-site link:
    if the page already points at greenhouse/lever/ashby, that IS the board and
    one hop lands on it. Same-domain links are kept as the fallback; anything
    else off-domain is dropped so we never wander onto a partner's site.
    """
    links = await page.evaluate("""() => [...document.querySelectorAll('a[href], iframe[src]')]
        .map(e => ({h: e.href || e.src || '', t: (e.textContent || '') + ' ' + (e.getAttribute('aria-label') || '')}))
        .filter(x => x.h.startsWith('http'))""")
    on_ats, on_site = [], []
    for link in links:
        href, text = link["h"], link["t"]
        if match_ats(href):
            on_ats.append(href)
        elif (_DEEPER.search(text) or _DEEPER.search(href)) and domain in href:
            on_site.append(href)
    ordered = on_ats + on_site
    return list(dict.fromkeys(ordered))[:3]  # dedupe, cap the crawl


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
            await settle(page)
        except Exception:
            pass

    async def scan_dom():
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
        if hits:
            return
        # Last resort on this page: scan the raw HTML. Sites that server-render
        # their board (REDACTED embeds the Ashby host inside its Next.js JSON payload)
        # expose the ATS nowhere in an element attribute and never call it from
        # the browser, so both the DOM sweep and the network listener miss it.
        try:
            m = match_ats(await page.content())
            if m:
                hits.append((m, "page html"))
        except Exception:
            pass

    await scan_dom()

    # Second hop: the careers page rendered but named no ATS. Follow the most
    # board-like links from it rather than concluding "no ATS" -- a marketing
    # landing page is the common case, not the exception.
    if not hits and career_url:
        try:
            for candidate in await deeper_board_links(page, domain):
                await page.goto(candidate, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                await dismiss_consent(page)
                await settle(page)
                await scan_dom()
                if hits:
                    career_url, via = candidate, f"{via}+deep"
                    break
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


def probe_unknowns(records, csv_path, lock):
    """Second pass: API-probe every company browsing could not classify."""
    todo = [(i, r) for i, r in enumerate(records)
            if r[3] in ("Unknown/Custom", "N/A", "ERROR", "")]
    if not todo:
        return records
    print(f"\nAPI-probing {len(todo)} unclassified compan(ies)...", flush=True)
    found = 0
    # Threaded: the probe is pure network wait, and sequentially it dominates the
    # run (each company can cost 16 requests). Workers are few and each still
    # pauses between its own calls, so the per-host rate stays polite.
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(probe_record, {"name": r[0], "website": r[1]}): i for i, r in todo}
        for done, fut in enumerate(concurrent.futures.as_completed(futures), start=1):
            i = futures[fut]
            hit = fut.result()
            if hit:
                found += 1
                r = records[i]
                records[i] = [hit["name"], hit["website"], r[2], hit["ats"],
                              hit["token"], hit["via"], hit["status"]]
                print(f"  probe HIT {hit['name']}: {hit['ats']}/{hit['token']}", flush=True)
            if done % 50 == 0:
                write_csv(csv_path, records, lock)
                print(f"  ...probed {done}/{len(todo)} ({found} recovered)", flush=True)
    write_csv(csv_path, records, lock)
    print(f"API probe recovered {found}/{len(todo)}.", flush=True)
    return records


# ---------------------------------------------------------------- API probe fallback
# Browsing answers "what does this page call?", which fails for companies that
# proxy their board server-side (REDACTED, REDACTED: no ATS string anywhere in the
# page). This asks the opposite question -- "does any plausible token answer on a
# V1 API?" -- and a 200 with postings is proof, not a guess. Verified recoveries
# this found that browsing could not: REDACTED, REDACTED, REDACTED, Redis, REDACTED, Render.
PROBE_APIS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{t}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{t}?mode=json",
    "lever-eu": "https://api.eu.lever.co/v0/postings/{t}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{t}?includeCompensation=true",
}
_PROBE_ATS = {"greenhouse": "Greenhouse", "lever": "Lever",
              "lever-eu": "Lever", "ashby": "Ashby"}


def _probe_count(source: str, payload) -> int:
    if source.startswith("lever"):
        return len(payload) if isinstance(payload, list) else 0
    if isinstance(payload, dict):
        return len(payload.get("jobs") or [])
    return 0


def probe_tokens(name: str, website: str) -> list[str]:
    """Plausible board tokens for a company, best first.

    Deliberately conservative: only forms a human would try. Non-obvious tokens
    (REDACTED, REDACTED) are unreachable this way, so a miss is never evidence
    the company has no board.
    """
    host = normalize_domain(website).removeprefix("www.")
    base = host.split(".")[0]
    clean = re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()
    out = [clean.replace(" ", ""), clean.replace(" ", "-"), base, host]
    seen, res = set(), []
    for c in out:
        if c and c not in seen and ref_ok("greenhouse", c):
            seen.add(c)
            res.append(c)
    return res[:4]


def probe_record(rec: dict, pause: float = 0.35) -> dict | None:
    """Return an updated record if a V1 API answers for this company, else None."""
    for tok in probe_tokens(rec["name"], rec["website"]):
        for source, tmpl in PROBE_APIS.items():
            try:
                time.sleep(pause)  # same courtesy interval as the ingest pipeline
                resp = requests.get(tmpl.format(t=tok), headers=HEADERS, timeout=20)
                if resp.status_code != 200 or _probe_count(source, resp.json()) == 0:
                    continue
            except Exception:
                continue
            return {**rec, "ats": _PROBE_ATS[source], "token": tok,
                    "via": "api-probe", "status": f"OK (api probe: {source})"}
    return None


def emit_ingestable(records, out_path):
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
        (clean if ref_ok(source, token) else review).append(row)
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["company_name", "source", "board_ref", "active", "tier", "notes"])
        for row in sorted(clean, key=lambda x: (x[1], x[0].lower())):
            w.writerow(row)
    return clean, review


def emit_inventory(records, out_path):
    """Every company on a *detected* ATS -> config/companies.csv schema.

    V1 (Greenhouse/Lever/Ashby) is active=true with a validated bare token; every
    other detected ATS is active=false inventory (ADR-0013). Custom / no-board
    companies are dropped (nothing to ingest). Returns (active_count, rows).
    """
    v1 = {"greenhouse", "lever", "ashby"}
    out, seen = [], set()
    for r in records:
        ats, token = r[3], (r[4] or "").strip()
        if ats in ("Unknown/Custom", "N/A", "ERROR", ""):
            continue
        source = re.sub(r"[^a-z0-9]", "", ats.lower())
        if source in v1:
            if not ref_ok(source, token):
                continue
            active, tier, ref = "true", "1", token
        else:
            active, tier, ref = "false", "2", token          # inventory; ref may be blank
        key = (source, ref.lower(), r[0].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append([r[0], source, ref, active, tier, f"{ats}; careers via {r[5]}"])
    out.sort(key=lambda x: (x[3] != "true", x[1], x[0].lower()))   # active first
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["company_name", "source", "board_ref", "active", "tier", "notes"])
        w.writerows(out)
    return sum(1 for x in out if x[3] == "true"), out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True,
                    help="source .xlsx with Company Name / Website columns")
    ap.add_argument("--sheet", default="Company List")
    ap.add_argument("--out", default="ats_audit_results.csv",
                    help="full audit CSV (default: cwd)")
    ap.add_argument("--ingestable", default="companies_ingestable.csv",
                    help="GH/Lever/Ashby rows in the config/companies.csv schema (default: cwd)")
    ap.add_argument("--inventory", default="companies_all.csv",
                    help="all detected-ATS companies (V1 active + inventory) in the same schema")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--checkpoint-every", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0, help="0 = all; else first N (for testing)")
    ap.add_argument("--no-probe", action="store_true",
                    help="skip the API-probe pass over companies browsing could not classify")
    args = ap.parse_args()

    companies = load_companies(args.xlsx, args.sheet)
    if args.limit:
        companies = companies[:args.limit]

    records = asyncio.run(run(companies, args.out, args.concurrency, args.checkpoint_every))
    if not args.no_probe:
        records = probe_unknowns(records, args.out, threading.Lock())

    from collections import Counter
    print("\nATS:", dict(Counter(r[3] for r in records if r[3])))
    print("Found via:", dict(Counter(r[5] for r in records if r[5])))
    clean, review = emit_ingestable(records, args.ingestable)
    active, inv = emit_inventory(records, args.inventory)
    print(f"\nIngestable (GH/Lever/Ashby): {len(clean)} clean, {len(review)} need review")
    print(f"Inventory: {len(inv)} rows ({active} active + {len(inv) - active} inventory)")
    print(f"  full audit  -> {args.out}")
    print(f"  ingestable  -> {args.ingestable}")
    print(f"  inventory   -> {args.inventory}")
    if review:
        print("  REVIEW (bad token capture):")
        for row in review:
            print(f"    {row[0]:26}{row[1]:11}board_ref={row[2]!r}")


if __name__ == "__main__":
    main()
