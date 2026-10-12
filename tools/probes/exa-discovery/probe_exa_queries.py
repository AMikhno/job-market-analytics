"""Does an embedding search API find boards the company list does not already have?

Question: searching ATS *posting* hosts returns URLs that carry the board_ref
directly (`.../<ref>/jobs/<id>`), so a hit is a candidate company with no render
and no token guessing. Two things decide whether that is worth building on:

  1. NOVELTY  -- what share of verified refs are companies the list has never seen?
  2. LIVENESS -- what share of extracted refs answer on a keyless V1 API today?

Index staleness does not threaten either: a long-dead posting still names a board
that gets fetched live afterwards. A dead *board* is caught by the liveness check.

Also reports the same funnel per query *variant*, which is the actual experiment --
`docs/research/relevance-signals.md` found requirements text beats titles 3/13 vs
1/21, predicting the stack- and responsibility-led variants should beat title-led.

Costs real money (~$0.007/search). Run --dry-run first; it prints the plan and spends
nothing. Queries live in a gitignored file because desired titles and tech are
preferences, and a preference is private config (ADR-0028).

    export EXA_API_KEY=...
    uv run python tools/probes/exa-discovery/probe_exa_queries.py --dry-run
    uv run python tools/probes/exa-discovery/probe_exa_queries.py
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import pathlib
import re
import sys
import time
import urllib.parse

import requests

ROOT = pathlib.Path(__file__).resolve().parents[3]
DISCOVERY = ROOT / "config" / "discovery"
UA = "job-market-analytics/0.1 (research probe; contact via repo)"
SEARCH_URL = "https://api.exa.ai/search"

# ---------------------------------------------------------------- ATS knowledge
# Deliberately duplicated from tools/company_discovery/ats_audit.py rather than
# imported: that module imports playwright/openpyxl at module scope, which are
# `--with` extras and absent from the project env this probe runs in. Same reason
# ats_audit.py duplicates V1_SOURCES instead of importing ingest.sources (ADR-0018).
# Only the hosts whose *posting* URLs carry the ref are listed.
# Each source lists the domains to pass to `includeDomains` and the pattern that
# recovers the ref from a posting URL. Boards on a per-company *subdomain* need the
# `*.example.com` wildcard form -- the docs list bare domains, path prefixes and
# subdomain wildcards as distinct filter kinds, and do not promise that a bare
# domain covers its subdomains. Getting this wrong silently returns nothing for
# three of the eight sources, which reads identically to "Exa found no boards".
POSTING_HOSTS = {
    "greenhouse": (["job-boards.greenhouse.io", "boards.greenhouse.io"],
                   r"greenhouse\.io/([\w.-]+)"),
    "lever": (["jobs.lever.co", "jobs.eu.lever.co"],
              r"jobs(?:\.eu)?\.lever\.co/([\w.-]+)"),
    "ashby": (["jobs.ashbyhq.com"], r"ashbyhq\.com/([\w.-]+(?: [\w.-]+)*)"),
    "workable": (["apply.workable.com"], r"apply\.workable\.com/([\w.-]+)"),
    "smartrecruiters": (["jobs.smartrecruiters.com"], r"smartrecruiters\.com/([\w.-]+)"),
    "recruitee": (["*.recruitee.com"], r"([\w-]+)\.recruitee\.com"),
    "bamboohr": (["*.bamboohr.com"], r"([\w-]+)\.bamboohr\.com"),
    "pinpoint": (["*.pinpointhq.com"], r"([\w-]+)\.pinpointhq\.com"),
}

VERIFY_APIS = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{t}/jobs", "jobs"),
    "lever": ("https://api.lever.co/v0/postings/{t}?mode=json", None),
    "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{t}", "jobs"),
    "workable": ("https://apply.workable.com/api/v1/widget/accounts/{t}", "jobs"),
    "smartrecruiters": (
        "https://api.smartrecruiters.com/v1/companies/{t}/postings?limit=1",
        "content",
    ),
    "recruitee": ("https://{t}.recruitee.com/api/offers/", "offers"),
    "bamboohr": ("https://{t}.bamboohr.com/careers/list", "result"),
    "pinpoint": ("https://{t}.pinpointhq.com/postings.json", "data"),
}

# Path fragments and API nouns that are never a board name. Mirrors _BAD_TOKENS in
# ats_audit.py -- the "junk refs from discovery" class in TODO.md is what happens
# when this list is short.
BAD = {
    "v1", "v0", "api", "embed", "jobs", "job", "boards", "board", "www", "posting-api",
    "job-board", "postings", "companies", "for", "js", "users", "user", "auth", "login",
    "signup", "account", "accounts", "career", "careers", "widget", "job-widget", "list",
    "offers", "detail", "search", "apply", "en", "en-us", "index",
}
BARE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ASHBY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?: [A-Za-z0-9._-]+)*$")


def ref_ok(source: str, token: str) -> bool:
    pattern = ASHBY if source == "ashby" else BARE
    return bool(pattern.fullmatch(token)) and token.lower() not in BAD


def extract_ref(url: str) -> tuple[str, str] | None:
    """(source, board_ref) from a posting URL, or None."""
    u = urllib.parse.unquote(url or "")
    for source, (_domains, pat) in POSTING_HOSTS.items():
        m = re.search(pat, u, re.I)
        if m and m.group(1):
            tok = m.group(1).strip()
            if ref_ok(source, tok):
                return source, tok
    return None


# ---------------------------------------------------------------- location gate
# The liveness check already downloads the board's postings, so the geography
# question is answerable for free -- and it is the one that matters. A US-only
# company costs nothing in the digest (allowed_locations filters its postings
# downstream) but is fetched daily forever for zero gold rows. Filter the *company*
# here rather than the posting later.
#
# Read from the same seed the warehouse uses, so this agrees with silver by
# construction. Gitignored (ADR-0028); absent means the gate is skipped, loudly.
LOC_KEYS = re.compile(r"location|city|region|state|country|workplace|remote|office", re.I)


def load_allowed() -> list[re.Pattern[str]]:
    seed = ROOT / "dbt" / "seeds" / "allowed_locations.csv"
    if not seed.exists():
        return []
    pats = []
    with open(seed, newline="") as f:
        for r in csv.DictReader(f):
            p = (r.get("pattern") or "").strip()
            if p:
                pats.append(re.compile(rf"\b{re.escape(p)}\b", re.I))
    return pats


def _all_strings(obj: object, depth: int = 0) -> str:
    """Every string in a subtree. Used once a location-ish key has matched -- the
    value below it is location data whatever its inner key names are (Greenhouse
    nests {"location": {"name": ...}}, so requiring the inner key to match too
    extracted nothing)."""
    if depth > 4 or isinstance(obj, bool):
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return " ".join(_all_strings(v, depth + 1) for v in obj.values())
    if isinstance(obj, list):
        return " ".join(_all_strings(x, depth + 1) for x in obj[:50])
    return ""


def _location_text(obj: object, depth: int = 0) -> str:
    """Every location-ish string in a posting payload, shape-agnostic.

    Each ATS nests location differently (a bare string, {city,region,country}, a
    {name} object), and several shapes are unverified. Walking for keys that look
    like a location beats eight guessed field paths that fail silently.
    """
    if depth > 4:
        return ""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if LOC_KEYS.search(str(k)):
                out.append(str(k) if isinstance(v, bool) and v else _all_strings(v))
            elif isinstance(v, (dict, list)):
                out.append(_location_text(v, depth + 1))
    elif isinstance(obj, list):
        for item in obj[:200]:
            out.append(_location_text(item, depth + 1))
    return " ".join(x for x in out if x)


# Geographies that will not hire here regardless of the role -- salary bands and
# timezone, not capability. Deliberately narrower than "not North America":
# UK, Ireland, Israel and Australia are NOT listed, so they fall to `watch` with
# their locations printed rather than being dropped on a guess. Under-filter and
# show the evidence; a silent drop is not a reviewable decision.
NON_VIABLE = re.compile(
    r"\b(india|bengaluru|bangalore|hyderabad|mumbai|delhi|noida|gurgaon|gurugram|pune|"
    r"chennai|kolkata|ahmedabad|china|beijing|shanghai|shenzhen|guangzhou|hangzhou|"
    r"singapore|japan|tokyo|korea|seoul|philippines|manila|vietnam|hanoi|indonesia|jakarta|"
    r"poland|warsaw|krakow|wroclaw|portugal|lisbon|porto|spain|madrid|barcelona|valencia|"
    r"germany|berlin|munich|hamburg|cologne|france|paris|lyon|netherlands|amsterdam|utrecht|"
    r"belgium|brussels|italy|milan|rome|greece|athens|ukraine|kyiv|lviv|romania|bucharest|"
    r"bulgaria|sofia|serbia|belgrade|croatia|zagreb|czech|prague|slovakia|hungary|budapest|"
    r"lithuania|vilnius|latvia|riga|estonia|tallinn|austria|vienna|switzerland|zurich|geneva|"
    r"sweden|stockholm|norway|oslo|denmark|copenhagen|finland|helsinki|turkey|istanbul|"
    r"brazil|sao paulo|mexico|argentina|colombia|egypt|nigeria|kenya|south africa)\b",
    re.I,
)


def verify(source: str, ref: str, allowed: list[re.Pattern[str]],
           pause: float = 0.3) -> tuple[int, int, str, str]:
    """(live postings, postings in range, tier, most common locations).

    Three tiers, because the company gate is broader than the posting gate:
      in-range  at least one posting matches allowed_locations -- takeable today
      watch     no match, but not clearly non-viable either (US, global, unknown).
                Kept: a US company opens Canadian and remote reqs later, and the
                silver location gate filters its postings anyway
      excluded  effectively every located posting is somewhere that will not hire
                from here -- adding it means fetching a board daily forever for zero

    0 live postings means do not propose it at all: an empty board is
    indistinguishable from a wrong one (the SmartRecruiters stub-account lesson).
    """
    tmpl, key = VERIFY_APIS[source]
    try:
        time.sleep(pause)
        resp = requests.get(tmpl.format(t=ref), headers={"User-Agent": UA}, timeout=20)
        if resp.status_code != 200:
            return 0, 0, "dead", ""
        payload = resp.json()
    except Exception:  # noqa: BLE001 - a probe: every outcome is data
        return 0, 0, "dead", ""
    if key is None:
        jobs = payload if isinstance(payload, list) else []
    else:
        jobs = (payload.get(key) or []) if isinstance(payload, dict) else []
    if not isinstance(jobs, list) or not jobs:
        return 0, 0, "dead", ""

    locs = [_location_text(j).strip() for j in jobs]
    located = [x for x in locs if x]
    top = "; ".join(f"{loc[:34]}({n})"
                    for loc, n in collections.Counter(located).most_common(3))
    if not allowed:
        return len(jobs), -1, "watch", top  # seed missing: gate not evaluated

    in_range = sum(1 for x in located if any(p.search(x) for p in allowed))
    if in_range:
        return len(jobs), in_range, "in-range", top
    bad = sum(1 for x in located if NON_VIABLE.search(x))
    if located and bad / len(located) >= 0.8:
        return len(jobs), 0, "excluded", top
    return len(jobs), 0, "watch", top


# ---------------------------------------------------------------- known state
def load_known() -> set[tuple[str, str]]:
    """(source, ref) pairs already in the master list or the audit cache."""
    known: set[tuple[str, str]] = set()
    master = ROOT / "config" / "companies.csv"
    if master.exists():
        with open(master, newline="") as f:
            for r in csv.DictReader(f):
                ref = (r.get("board_ref") or "").strip().lower()
                if ref:
                    known.add(((r.get("source") or "").strip().lower(), ref))
    cache = DISCOVERY / "ats_audit_results.csv"
    if cache.exists():
        with open(cache, newline="") as f:
            for r in csv.DictReader(f):
                ref = (r.get("Board Token") or "").strip().lower()
                ats = re.sub(r"[^a-z0-9]", "", (r.get("Detected ATS") or "").lower())
                if ref:
                    known.add((ats, ref))
    return known


def search(query: str, key: str, num: int, mode: str, hosts: list[str]) -> tuple[list[str], float]:
    body = {
        "query": query,
        "numResults": num,
        "type": mode,
        "includeDomains": hosts,
    }
    resp = requests.post(
        SEARCH_URL,
        json=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": UA},
        timeout=45,
    )
    resp.raise_for_status()
    payload = resp.json()
    urls = [r.get("url", "") for r in payload.get("results", [])]
    # Exa reports what it actually charged; prefer it over our estimate.
    spent = float((payload.get("costDollars") or {}).get("total") or 0.0)
    return urls, spent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", default=str(DISCOVERY / "exa_queries.json"))
    ap.add_argument("--num-results", type=int, default=25)
    ap.add_argument("--mode", default="auto", choices=["auto", "neural", "keyword"])
    ap.add_argument("--max-queries", type=int, default=40, help="hard spend cap")
    ap.add_argument("--only-source", default="", choices=[""] + list(POSTING_HOSTS),
                    help="restrict includeDomains to one source (diagnosing a source "
                         "that returned nothing)")
    ap.add_argument("--bare-domains", action="store_true",
                    help="send `example.com` instead of `*.example.com`. Pair with "
                         "--only-source to tell 'wildcard does not match' apart from "
                         "'Exa has not indexed these boards'")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, spend nothing")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    # A diagnostic run writes somewhere else by default. Sharing the full run's
    # output path meant a 3-query source check silently replaced a 395-row result
    # set with its own 4 rows -- and the full run costs $0.70 to reproduce.
    if not args.out:
        diag = args.only_source or ("bare" if args.bare_domains else "")
        args.out = str(DISCOVERY / (f"exa_diag_{diag}.csv" if diag
                                    else "exa_pilot_candidates.csv"))

    qpath = pathlib.Path(args.queries)
    if not qpath.exists():
        print(f"no query file at {qpath} -- see the example in this directory's README",
              file=sys.stderr)
        return 2
    variants: dict[str, list[str]] = json.loads(qpath.read_text())["variants"]

    full = [(v, q) for v, qs in variants.items() for q in qs]
    plan = full[: args.max_queries]
    if len(plan) < len(full):
        # The cap slices in variant order, so a truncated run drops whole trailing
        # variants rather than sampling -- which silently deletes an arm of the
        # experiment. Say so instead of reporting a comparison that never ran.
        dropped = sorted({v for v, _ in full[args.max_queries:]} - {v for v, _ in plan})
        print(f"WARNING: --max-queries={args.max_queries} truncates {len(full)} queries; "
              f"variants entirely dropped: {', '.join(dropped) or 'none'}\n")
    selected = ({args.only_source: POSTING_HOSTS[args.only_source]}
                if args.only_source else POSTING_HOSTS)
    hosts = sorted({d for domains, _ in selected.values() for d in domains})
    if args.bare_domains:
        hosts = sorted({h.removeprefix("*.") for h in hosts})
    print(f"{len(plan)} queries x {args.num_results} results, mode={args.mode}")
    print(f"hosts: {', '.join(hosts)}")
    # $7/1k requests covers the first 10 results; every result above 10 adds
    # $1/1k. Measured 2026-09-01: 32 queries at numResults=25 billed $0.704,
    # exactly 32 x (0.007 + 15 x 0.001). Costing a query as a flat $0.007
    # under-estimated the run by 3.2x.
    per_query = 0.007 + max(args.num_results - 10, 0) * 0.001
    print(f"estimated spend: ~${len(plan) * per_query:.2f} "
          f"(${per_query:.3f}/query at {args.num_results} results)\n")
    if args.dry_run:
        for variant, q in plan:
            print(f"  [{variant}] {q}")
        return 0

    key = os.environ.get("EXA_API_KEY", "")
    if not key:
        print("set EXA_API_KEY in your environment", file=sys.stderr)
        return 2

    known = load_known()
    allowed = load_allowed()
    print(f"{len(known)} (source, ref) pairs already known")
    if allowed:
        print(f"location gate: {len(allowed)} allowed patterns\n")
    else:
        print("location gate: SKIPPED -- dbt/seeds/allowed_locations.csv not found\n")

    funnel: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    found: dict[tuple[str, str], dict] = {}
    spent = 0.0

    for variant, query in plan:
        try:
            urls, cost = search(query, key, args.num_results, args.mode, hosts)
        except Exception as exc:  # noqa: BLE001 - a probe: every outcome is data
            print(f"  [{variant}] SEARCH FAILED ({type(exc).__name__}): {query[:60]}")
            funnel[variant]["failed"] += 1
            continue
        spent += cost
        f = funnel[variant]
        f["queries"] += 1
        f["results"] += len(urls)

        for url in urls:
            hit = extract_ref(url)
            if not hit:
                continue
            f["parsed"] += 1
            source, ref = hit
            pair = (source, ref.lower())
            if pair in known:
                f["known"] += 1
                continue
            f["new"] += 1
            if pair in found:
                found[pair]["seen_by"].add(variant)
                continue
            n, ok, tier, top = verify(source, ref, allowed)
            if n:
                f["verified"] += 1
                f[tier] += 1
                if tier != "excluded":
                    f["keep"] += 1
                found[pair] = {"source": source, "ref": ref, "postings": n,
                               "in_range": ok, "tier": tier, "top": top,
                               "url": url, "seen_by": {variant}}
            else:
                f["dead"] += 1
        print(f"  [{variant}] {len(urls):>3} results -> {f['new']:>3} new "
              f"({f['verified']} verified so far)  {query[:52]}")
        time.sleep(0.5)

    # ---------------------------------------------------------------- report
    print("\n" + "=" * 92)
    print(f"{'variant':<22}{'qry':>5}{'res':>6}{'parsed':>8}{'new':>6}{'live':>6}"
          f"{'in-range':>10}{'watch':>7}{'excl':>6}{'keep/qry':>10}")
    print("-" * 92)
    for variant in variants:
        f = funnel[variant]
        if not f["queries"]:
            continue
        print(f"{variant:<22}{f['queries']:>5}{f['results']:>6}{f['parsed']:>8}"
              f"{f['new']:>6}{f['verified']:>6}{f['in-range']:>10}{f['watch']:>7}"
              f"{f['excluded']:>6}{f['keep'] / f['queries']:>10.1f}")
    tot = sum(funnel.values(), collections.Counter())
    print("-" * 92)
    print(f"{'TOTAL':<22}{tot['queries']:>5}{tot['results']:>6}{tot['parsed']:>8}"
          f"{tot['new']:>6}{tot['verified']:>6}{tot['in-range']:>10}{tot['watch']:>7}"
          f"{tot['excluded']:>6}")
    if tot["results"]:
        print(f"\nextraction rate {tot['parsed'] / tot['results']:.0%}   "
              f"novelty {tot['new'] / max(tot['parsed'], 1):.0%}   "
              f"liveness {tot['verified'] / max(tot['new'], 1):.0%}   "
              f"in-range {tot['in-range'] / max(tot['verified'], 1):.0%}")
    print(f"actual spend: ${spent:.3f}   new live boards: {len(found)}   "
          f"keep (in-range + watch): {tot['keep']}")

    if found:
        pathlib.Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["tier", "source", "board_ref", "live_postings", "postings_in_range",
                        "top_locations", "found_by", "example_url"])
            # in-range, then watch, then excluded. Excluded rows stay in the file
            # rather than being dropped: the tier is a heuristic over location
            # strings, and top_locations is the evidence for overruling it.
            order = {"in-range": 0, "watch": 1, "excluded": 2}
            for rec in sorted(found.values(),
                              key=lambda r: (order[r["tier"]], r["source"], r["ref"])):
                w.writerow([rec["tier"], rec["source"], rec["ref"], rec["postings"],
                            rec["in_range"], rec["top"],
                            "|".join(sorted(rec["seen_by"])), rec["url"]])
        print(f"candidates -> {args.out}")
        print("\nGATE (pre-registered): novelty >=30% and >=1/3 of verified boards in range.")
        print("Eyeball the CSV for portals/directories before believing it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
