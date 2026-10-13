"""Confirm a candidate website really belongs to the board we hold — without a browser.

`ats_audit.py` renders each site because it is *discovering* an unknown board token.
This is the easier problem: the ref is already known, so the question is only whether
the site mentions it. A plain `requests.get` plus a string search answers that, which
matters because the rendering path is currently unavailable on macOS 13 — Playwright's
current build dropped the platform and the last version that supported it needs a
`greenlet` that will not compile on Python 3.14.

Three outcomes, and the distinction between the last two is the point:

  confirmed   the site (or its careers page) names this exact board ref
  wrong       the site names a *different* board on the same ATS -- the domain guess
              landed on someone else, so the website is cleared rather than kept
  unproven    nothing found. Common and not a failure: a JS-injected board is
              invisible to this method. Left blank, because `website` is the key that
              re-derives a ref after an ATS move (CLAUDE.md) and a plausible wrong one
              is worse than an empty one.

    uv run python tools/probes/exa-discovery/verify_websites.py
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import csv
import pathlib
import re
import sys
import urllib.parse

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = pathlib.Path(__file__).resolve().parents[3]
DISCOVERY = ROOT / "config" / "discovery"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}

# Where a ref appears in markup, per ATS. Captures the ref so a *different* board on
# the same vendor is distinguishable from no board at all.
REF_IN_PAGE = {
    "greenhouse": r"greenhouse\.io/(?:embed/job_board\?for=|v1/boards/)?([\w.-]+)",
    "lever": r"(?:jobs|api)(?:\.eu)?\.lever\.co/([\w.-]+)",
    "ashby": r"ashbyhq\.com/(?:posting-api/job-board/)?([\w.%-]+)",
    "workable": r"apply\.workable\.com/([\w.-]+)",
    "recruitee": r"([\w-]+)\.recruitee\.com",
    "pinpoint": r"([\w-]+)\.pinpointhq\.com",
    "smartrecruiters": r"smartrecruiters\.com/(?:v1/companies/)?([\w.-]+)",
    "bamboohr": r"([\w-]+)\.bamboohr\.com",
}
PATHS = ("", "/careers", "/careers/", "/jobs", "/about/careers", "/company/careers",
         "/join-us", "/careers/open-roles", "/en/careers")

# The vendor-hosted board page, which usually links back to the company's own site:
# a header logo, a "back to <company>" link, or the org's theme config. That is a far
# better source than guessing a TLD -- guessing got `<ref>.com` for companies that
# actually sit on `.ca`, `.app`, `.bio` and `.io`, and missed two that trade under a
# different name than their board ref entirely.
BOARD_PAGE = {
    "ashby": "https://jobs.ashbyhq.com/{r}",
    "lever": "https://jobs.lever.co/{r}",
    "greenhouse": "https://job-boards.greenhouse.io/{r}",
    "workable": "https://apply.workable.com/{r}/",
    "recruitee": "https://{r}.recruitee.com/",
    "pinpoint": "https://{r}.pinpointhq.com/",
    "smartrecruiters": "https://jobs.smartrecruiters.com/{r}",
}

# Hosts that are never the employer. Deliberately expressed as *shapes* rather than
# as a roster of third-party vendors: a roster is unbounded, needs a public repo to
# name companies, and dates. These substrings cover the serving infrastructure, the
# analytics and consent layer, and the standards bodies that appear in every XML
# namespace. The ATS vendors are derived from BOARD_PAGE so a new source is added in
# one place, and whatever this misses is handled by ranking, which prefers a host
# echoing the board ref over a stranger that merely appears on the page.
INFRA_SHAPES = (
    "cdn", "static", "assets", "images", "img.", "media.", "fonts", "js.", "css.",
    "analytics", "tagmanager", "tags.", "tracking", "pixel", "metrics", "telemetry",
    "cookie", "consent", "captcha", "widget", "embed", "api.", "s3.", "storage.",
    ".w3.", "schema.org", "ogp.me", "purl.org", "xmlns", "example.com",
)
# The vendors' *names*, not their board hosts: an ATS serves its board from one
# domain and its own marketing site from another TLD, and the second one appears in
# the page footer. Comparing the registrable label catches both.
VENDOR_NAMES = tuple(sorted({
    urllib.parse.urlparse(t.format(r="x")).netloc.lower().split(".")[-2]
    for t in BOARD_PAGE.values()
}))


def is_infra(host: str) -> bool:
    if any(s in host for s in INFRA_SHAPES):
        return True
    labels = host.lower().split(".")
    return any(v in labels for v in VENDOR_NAMES)


def _rank(host: str, ref: str) -> tuple[int, int]:
    """Sort key for a candidate host; lower is better.

    Two preferences, in order: a host whose stem echoes the board ref is the
    company far more often than a third party that merely appears on the page, and
    an apex beats a `careers.`/`jobs.`/`assets.` subdomain because the apex is what
    survives as a recovery key.
    """
    bare = host.removeprefix("www.")
    stem = bare.split(".")[0]
    key = re.sub(r"[^a-z0-9]", "", ref.lower())
    echo = 0 if (key and (key in re.sub(r"[^a-z0-9]", "", bare)
                          or re.sub(r"[^a-z0-9]", "", stem) in key)) else 1
    sub = 1 if bare.count(".") > 1 else 0
    return (echo, sub)


def website_from_board(source: str, ref: str) -> str:
    """The company's own domain, read off the vendor-hosted board page."""
    tmpl = BOARD_PAGE.get(source)
    if not tmpl:
        return ""
    html = fetch(tmpl.format(r=urllib.parse.quote(ref)), timeout=20)
    if not html:
        return ""
    counts: collections.Counter = collections.Counter()
    for host in re.findall(r"https?://([\w.-]+\.[a-z]{2,})", html):
        host = host.lower()
        if len(host) > 4 and not is_infra(host):
            counts[host] += 1
    if not counts:
        return ""
    best = sorted(counts, key=lambda h: (_rank(h, ref), -counts[h]))[0]
    return best.removeprefix("www.")


def fetch(url: str, timeout: int = 12) -> str:
    try:
        resp = requests.get(url, headers=UA, timeout=timeout, verify=False,
                            allow_redirects=True)
        return resp.text if resp.status_code < 400 else ""
    except Exception:  # noqa: BLE001 - a probe: every outcome is data
        return ""


def check(domain: str, source: str, ref: str) -> tuple[str, str]:
    """(verdict, evidence) for one candidate website."""
    pattern = REF_IN_PAGE.get(source)
    if not pattern or not domain:
        return "unproven", ""
    rx = re.compile(pattern, re.I)
    want = ref.lower()
    seen: set[str] = set()
    for path in PATHS:
        html = urllib.parse.unquote(fetch(f"https://{domain}{path}"))
        if not html:
            continue
        for m in rx.finditer(html):
            found = (m.group(1) or "").strip().lower()
            if not found:
                continue
            if found == want:
                return "confirmed", f"{domain}{path}"
            seen.add(found)
        # A careers link pointing off-site is worth one hop, but only to the vendor
        # host -- never onto an arbitrary third-party domain.
        if path == "" and not seen:
            for href in re.findall(r'href=["\']([^"\']+careers?[^"\']*)["\']', html, re.I)[:3]:
                nxt = urllib.parse.urljoin(f"https://{domain}", href)
                if urllib.parse.urlparse(nxt).netloc.lower().endswith(domain):
                    sub = urllib.parse.unquote(fetch(nxt))
                    for m in rx.finditer(sub):
                        found = (m.group(1) or "").strip().lower()
                        if found == want:
                            return "confirmed", nxt
                        if found:
                            seen.add(found)
    if seen:
        return "wrong", f"names {sorted(seen)[0]} instead"
    return "unproven", ""


def name_domains(name: str) -> list[str]:
    """Domain candidates from a company name, best first.

    A name that already carries a TLD is itself one; otherwise try the name
    joined, then without a trailing corporate suffix, then initials for a
    multi-word name that a company often shortens.
    """
    raw = (name or "").strip()
    if not raw:
        return []
    if re.fullmatch(r"[\w-]+\.(com|ca|io|ai|co|app|net|org|dev|bio)", raw, re.I):
        return [raw.lower()]
    words = [w for w in re.split(r"[^A-Za-z0-9]+", raw) if w]
    drop = {"inc", "llc", "ltd", "limited", "corp", "corporation", "co", "group",
            "holdings", "sa", "ag", "gmbh", "plc", "portal", "recruiting", "jobs",
            "postings", "careers", "the"}
    core = [w for w in words if w.lower() not in drop] or words
    stems = [
        "".join(core).lower(),
        "".join(core[:1]).lower() if len(core) > 1 else "",
        "".join(w[0] for w in core).lower() if len(core) > 2 else "",
    ]
    out = []
    for stem in dict.fromkeys(s for s in stems if len(s) > 2):
        out += [f"{stem}{t}" for t in (".com", ".ca", ".io", ".ai", ".co", ".net")]
    return out[:14]


def first_live(domains: list[str], source: str, ref: str) -> str:
    """The first candidate whose site names this board. Verification, not resolution:
    a name-derived domain is a guess, so only a round-trip match earns it a row."""
    for domain in domains:
        verdict, _evidence = check(domain, source, ref)
        if verdict == "confirmed":
            return domain
    return ""


def site_is_company(domain: str, name: str) -> bool:
    """Does this domain's own homepage identify as this company?

    A weaker check than the board round-trip and a different one: the column means
    "this company's website", not "the site that embeds this board". A careers page
    that injects its board with JavaScript defeats the round-trip while the homepage
    still says plainly whose it is, which is the only question the column asks.
    """
    if not domain or not name:
        return False
    html = fetch(f"https://{domain}", timeout=12)
    if not html:
        return False
    marks = []
    for pat in (r"<title[^>]*>([^<]{2,120})</title>",
                r'property=["\']og:site_name["\'][^>]*content=["\']([^"\']+)',
                r'content=["\']([^"\']+)["\'][^>]*property=["\']og:site_name["\']'):
        marks += re.findall(pat, html, re.I)
    def squash(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())
    want = squash(re.sub(r"\b(inc|llc|ltd|corp|group|portal|recruiting)\b\.?", "", name, flags=re.I))
    return bool(want) and any(want in squash(m) for m in marks)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", default=str(DISCOVERY / "companies_from_search_named.csv"))
    ap.add_argument("--out", default="")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    out = args.out or args.rows

    with open(args.rows, newline="") as f:
        rows = list(csv.DictReader(f))
    todo = [r for r in rows if (r.get("website") or "").strip()]
    print(f"{len(rows)} board(s), {len(todo)} with a website candidate to verify\n")

    # Pass 1: ask the board page what the company's domain is. It beats the
    # ref-derived candidate whenever the two disagree, because one is read off the
    # company's own board and the other is a guess about its TLD.
    print("reading each board page for the company's own domain...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        found = dict(zip(
            range(len(rows)),
            pool.map(lambda r: website_from_board(r["source"], r["board_ref"]), rows),
        ))
    moved = 0
    for i, row in enumerate(rows):
        site = found.get(i, "")
        if site and site != (row.get("website") or "").strip().lower():
            if row.get("website"):
                moved += 1
            row["website"] = site
    print(f"  {sum(1 for v in found.values() if v)} domain(s) read off a board page "
          f"({moved} replaced a weaker guess)\n")

    # Pass 2: for anything still blank, derive candidates from the company *name*
    # rather than the ref. Where the name came from the board API it is the
    # company's own, so a name already carrying a TLD, or one whose domain is its
    # initials, yields a domain no amount of TLD-guessing at the ref ever would.
    # Anything the board page could not answer, regardless of what a TLD guess left
    # in the column: that guess is unverified, and a name-derived candidate has to
    # pass a round-trip before it is written, so it is the stronger of the two.
    blanks = [i for i, r in enumerate(rows) if not found.get(i)]
    if blanks:
        print(f"deriving candidates from the company name for {len(blanks)} row(s)...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            got = dict(zip(blanks, pool.map(
                lambda i: first_live(name_domains(rows[i]["company_name"]),
                                     rows[i]["source"], rows[i]["board_ref"]),
                blanks)))
        for i, site in got.items():
            if site:
                rows[i]["website"] = site
        print(f"  {sum(1 for v in got.values() if v)} resolved\n")

    todo = [r for r in rows if (r.get("website") or "").strip()]
    print(f"verifying {len(todo)} candidate(s) by round-trip\n")

    results: dict[int, tuple[str, str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(check, r["website"].strip().lower(), r["source"],
                               r["board_ref"]): i
                   for i, r in enumerate(rows) if (r.get("website") or "").strip()}
        for done, fut in enumerate(concurrent.futures.as_completed(futures), start=1):
            results[futures[fut]] = fut.result()
            if done % 15 == 0:
                print(f"  ...{done}/{len(futures)}", flush=True)

    stats: collections.Counter = collections.Counter()
    for i, row in enumerate(rows):
        verdict, evidence = results.get(i, ("unproven", ""))
        from_board = bool(found.get(i))
        note = re.sub(r"website [^;]*", "", row["notes"]).strip("; ")
        if verdict == "confirmed":
            stats["confirmed both ways" if from_board else "confirmed by round-trip"] += 1
            row["notes"] = f"{note}; website confirmed on {evidence}"
        elif verdict == "wrong":
            # The board page named this domain and the domain names another board.
            # Keep it and say so: a company can run two boards on one ATS, and the
            # evidence belongs in front of a human rather than deleted.
            stats["board page vs site disagree" if from_board else "cleared, wrong board"] += 1
            if from_board:
                row["notes"] = f"{note}; website from the board page, but the site {evidence}"
            else:
                row["website"] = ""
                row["notes"] = f"{note}; website candidate {evidence} — cleared"
        elif from_board:
            # Read off the company's own board page but not re-findable from the
            # site, which is what a JS-injected careers page looks like. The source
            # is good enough to keep.
            stats["from board page, site not readable"] += 1
            row["notes"] = f"{note}; website read off the board page, site not machine-readable"
        else:
            # Last resort: the board was never findable from the site, but the site
            # may still identify as this company — which is all the column claims.
            cand = next((d for d in name_domains(row["company_name"])
                         if site_is_company(d, row["company_name"])), "")
            if cand:
                stats["homepage identifies the company"] += 1
                row["website"] = cand
                row["notes"] = f"{note}; website matched on the homepage, board not readable there"
            else:
                stats["no evidence"] += 1
                row["website"] = ""
                row["notes"] = f"{note}; website unknown — fill from a later audit"

    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"\n{dict(stats)}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
