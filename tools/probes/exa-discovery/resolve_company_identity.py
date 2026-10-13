"""Fill `company_name` and `website` on search-discovered rows, then prepare the audit.

Search gives a board ref, which is neither. A ref-derived name is a guess and a blank
`website` is a hole in the recovery key that re-derives a ref after an ATS move
(CLAUDE.md), so both are worth resolving properly rather than merging as placeholders.

Three sources, best first:

  1. **The board API.** Greenhouse and Workable return the company's own name for a
     board token. That is authoritative — no guessing.
  2. **The posting URL host.** Some boards publish postings on the company's domain
     rather than the vendor's, which gives `website` directly.
  3. **A TLD sweep over the ref.** Only ever a *candidate*: confirmed by the audit
     below, never written on its own.

The output is an .xlsx for `make discover`. That is the verification step and the
reason this is not just guessing: `ats_audit.py` renders each candidate site, finds
its careers page and reads the ATS it actually calls. When the board it lands on is
the one already held, the website is confirmed by round-trip. When it lands somewhere
else, the guess was wrong and the row says so.

    uv run python tools/probes/exa-discovery/resolve_company_identity.py
    make discover XLSX=config/discovery/identity_candidates.xlsx
    uv run python tools/probes/exa-discovery/resolve_company_identity.py --merge-audit
"""

from __future__ import annotations

import argparse
import collections
import csv
import pathlib
import re
import socket
import sys
import time
import urllib.parse

import requests

ROOT = pathlib.Path(__file__).resolve().parents[3]
DISCOVERY = ROOT / "config" / "discovery"
UA = {"User-Agent": "job-market-analytics/0.1 (research probe; contact via repo)"}

ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "recruitee.com",
             "pinpointhq.com", "smartrecruiters.com", "bamboohr.com", "rippling.com")

# Board endpoints that return the company's own name. Absent here means the ATS
# publishes postings but no board-level identity, so the name stays a candidate.
NAME_APIS = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{t}", "name"),
    "workable": ("https://apply.workable.com/api/v1/widget/accounts/{t}", "name"),
}

# Ordered by how often a company of this shape actually sits there.
TLDS = (".com", ".io", ".ai", ".ca", ".co", ".app", ".dev", ".net", ".org")


def board_name(source: str, ref: str) -> str:
    spec = NAME_APIS.get(source)
    if not spec:
        return ""
    url, key = spec
    try:
        resp = requests.get(url.format(t=ref), headers=UA, timeout=15)
        if resp.status_code != 200:
            return ""
        value = resp.json().get(key)
    except Exception:  # noqa: BLE001 - a probe: every outcome is data
        return ""
    return value.strip() if isinstance(value, str) else ""


def prettify(ref: str) -> str:
    words = re.split(r"[-_.\s]+", ref.strip())
    return " ".join(w if w.isupper() else w.capitalize() for w in words if w)


def own_domain(urls: list[str]) -> str:
    """A posting URL host that is not the ATS vendor's — the company's own domain."""
    for url in urls:
        host = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
        if host and not any(a in host for a in ATS_HOSTS):
            # careers.example.com -> example.com; a bare apex is the recovery key.
            parts = host.split(".")
            return ".".join(parts[-3:]) if parts[0] in ("careers", "jobs", "apply") and len(parts) > 2 else host
    return ""


def candidate_domains(ref: str) -> list[str]:
    """Plausible domains for a board ref, best first. Resolution only — not proof."""
    base = re.sub(r"[^a-z0-9]", "", ref.lower())
    if not base:
        return []
    hyphen = re.sub(r"[^a-z0-9-]", "", ref.lower()).strip("-")
    out = []
    for stem in dict.fromkeys([base, hyphen]):
        # A ref that already carries a TLD is the domain, so take it as-is.
        if re.search(r"\.(com|io|ai|ca|co|app|dev|net|org)$", ref.lower()):
            out.append(ref.lower())
            break
        out += [f"{stem}{t}" for t in TLDS]
    return list(dict.fromkeys(out))[:12]


def resolves(domain: str) -> bool:
    try:
        socket.getaddrinfo(domain, 443, proto=socket.IPPROTO_TCP)
        return True
    except OSError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default=str(DISCOVERY / "companies_from_search.csv"))
    ap.add_argument("--roles", default=str(DISCOVERY / "exa_pilot_roles.csv"))
    ap.add_argument("--out-csv", default=str(DISCOVERY / "companies_from_search_named.csv"))
    ap.add_argument("--out-xlsx", default=str(DISCOVERY / "identity_candidates.xlsx"))
    ap.add_argument("--drop", default="", help="comma-separated source/ref to remove outright")
    ap.add_argument("--merge-audit", action="store_true",
                    help="second pass: fold `make discover` results back in, keeping only "
                         "websites whose audited board matches the ref already held")
    ap.add_argument("--audit", default=str(DISCOVERY / "ats_audit_results.csv"))
    args = ap.parse_args()

    with open(args.candidates, newline="") as f:
        rows = list(csv.DictReader(f))
    drop = {d.strip().lower() for d in args.drop.split(",") if d.strip()}
    if drop:
        before = len(rows)
        rows = [r for r in rows
                if f"{r['source']}/{r['board_ref']}".lower() not in drop]
        print(f"dropped {before - len(rows)} row(s) on request\n")

    if args.merge_audit:
        return merge_audit(rows, args)

    with open(args.roles, newline="") as f:
        urls = collections.defaultdict(list)
        for r in csv.DictReader(f):
            urls[(r["source"], r["board_ref"])].append(r["url"])

    print(f"resolving identity for {len(rows)} board(s)\n")
    stats: collections.Counter = collections.Counter()
    for i, r in enumerate(rows, start=1):
        key = (r["source"], r["board_ref"])
        name = board_name(*key)
        if name:
            stats["name from board API"] += 1
        else:
            name = prettify(r["board_ref"])
            stats["name derived from ref"] += 1

        site = own_domain(urls.get(key, []))
        if site:
            stats["website from posting URL"] += 1
        else:
            site = next((d for d in candidate_domains(r["board_ref"]) if resolves(d)), "")
            stats["website candidate resolves" if site else "no website candidate"] += 1

        r["company_name"] = name
        r["website"] = site
        r["_confirmed"] = "api" if stats and board_name else ""
        time.sleep(0.2)
        if i % 20 == 0:
            print(f"  ...{i}/{len(rows)}", flush=True)

    for row in rows:
        row.pop("_confirmed", None)
    with open(args.out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    try:
        import openpyxl
    except ImportError:
        print("\nopenpyxl not available here — run the xlsx step with:")
        print("  uv run --with openpyxl --no-project python " + __file__)
        return 1
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Company List"
    ws.append(["Company Name", "Website"])
    for r in rows:
        if r["website"]:
            ws.append([r["company_name"], r["website"]])
    wb.save(args.out_xlsx)

    print(f"\n{dict(stats)}")
    print(f"-> {args.out_csv}")
    print(f"-> {args.out_xlsx}  ({ws.max_row - 1} rows with a website to audit)")
    print("\nNext, to confirm each website really leads to the board we hold:")
    print(f"  make discover XLSX={args.out_xlsx}")
    print(f"  uv run python {pathlib.Path(__file__).name} --merge-audit")
    return 0


def merge_audit(rows: list[dict], args) -> int:
    """Keep a website only when the audit lands on the board ref already held.

    The audit answers "what board does this site call?". Where that equals the ref
    search found, the two independent paths agree and the website is confirmed.
    Where it differs, the guessed domain belongs to someone else — a blank is the
    honest answer, and better than a plausible wrong one in the recovery key.
    """
    path = pathlib.Path(args.audit)
    if not path.exists():
        print(f"no audit at {path} — run `make discover` first", file=sys.stderr)
        return 2
    with open(path, newline="") as f:
        audited = {}
        for a in csv.DictReader(f):
            site = (a.get("Website") or "").strip().lower().removeprefix("www.")
            if site:
                audited[site] = (re.sub(r"[^a-z0-9]", "", (a.get("Detected ATS") or "").lower()),
                                 (a.get("Board Token") or "").strip().lower())

    kept = cleared = unseen = 0
    for r in rows:
        site = (r.get("website") or "").strip().lower().removeprefix("www.")
        if not site:
            continue
        hit = audited.get(site)
        if hit is None:
            unseen += 1
            continue
        if hit[0] == r["source"].lower() and hit[1] == r["board_ref"].lower():
            kept += 1
            r["notes"] = re.sub(r"website unknown[^;]*", "website confirmed by audit",
                                r["notes"])
        else:
            cleared += 1
            r["website"] = ""
            r["notes"] = re.sub(r"website unknown[^;]*",
                                "website candidate led to a different board — left blank",
                                r["notes"])

    out = pathlib.Path(args.out_csv)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"confirmed {kept}, cleared {cleared} wrong guess(es), {unseen} not in the audit")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
