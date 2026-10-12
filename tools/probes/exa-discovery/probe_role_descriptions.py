"""Fetch descriptions for discovered roles, then test the seeds against real labels.

Two things a title cannot answer, both of which showed up in the first labelling
pass: whether a deal-breaker is present ("Power BI needed", "JS pure DE role") and
whether the desired stack is actually there. Both live in the posting body.

So this re-fetches each board with descriptions enabled, matches postings back to
the roles file by URL, and — where a verdict exists — reports whether the existing
`deal_breaker_tech` and `desired_tech` seeds separate the yes/maybe from the no.
That is a measurement of the seeds against human judgement, and it needs no
warehouse and no model.

Free: keyless ATS endpoints only.

    uv run python tools/probes/exa-discovery/probe_role_descriptions.py
"""

from __future__ import annotations

import argparse
import collections
import csv
import html
import importlib.util
import pathlib
import re
import sys
import time

import requests

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DISCOVERY = ROOT / "config" / "discovery"
UA = "job-market-analytics/0.1 (research probe; contact via repo)"

_spec = importlib.util.spec_from_file_location("_q", HERE / "probe_exa_queries.py")
assert _spec and _spec.loader
Q = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(Q)

_spec2 = importlib.util.spec_from_file_location("_a", HERE / "probe_label_analysis.py")
assert _spec2 and _spec2.loader
A = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(A)

# List endpoints that carry the description inline. Greenhouse needs content=true;
# without it the list returns metadata only. Sources absent here need a per-posting
# detail call (ADR-0021) and are reported as "no description available" rather than
# silently scored as if the text were empty.
DESC_APIS = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{t}/jobs?content=true", "jobs"),
    "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{t}?includeCompensation=true",
              "jobs"),
    "lever": ("https://api.lever.co/v0/postings/{t}?mode=json", None),
    "workable": ("https://apply.workable.com/api/v1/widget/accounts/{t}?details=true", "jobs"),
    "recruitee": ("https://{t}.recruitee.com/api/offers/", "offers"),
}

DESC_KEYS = re.compile(r"descript|content|body|jobdescription|requirement", re.I)
TAG = re.compile(r"<[^>]+>")


def description_text(job: dict) -> str:
    """Every description-ish string on a posting, tags stripped.

    Key names differ per ATS (`content`, `descriptionHtml`, `descriptionPlain`,
    `description`) and some nest it. Walking for description-shaped keys beats five
    guessed field paths that fail silently -- the same tactic the location gate uses.
    """
    parts: list[str] = []

    def walk(obj: object, depth: int = 0) -> None:
        if depth > 3:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                if DESC_KEYS.search(str(k)):
                    parts.append(A_all_strings(v))
                elif isinstance(v, (dict, list)):
                    walk(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj[:50]:
                walk(item, depth + 1)

    def A_all_strings(obj: object, depth: int = 0) -> str:
        if depth > 3 or isinstance(obj, bool):
            return ""
        if isinstance(obj, str):
            return obj
        if isinstance(obj, dict):
            return " ".join(A_all_strings(v, depth + 1) for v in obj.values())
        if isinstance(obj, list):
            return " ".join(A_all_strings(x, depth + 1) for x in obj[:20])
        return ""

    walk(job)
    text = html.unescape(TAG.sub(" ", " ".join(parts)))
    return re.sub(r"\s+", " ", text).strip()


def load_seed(name: str, col: str) -> list[tuple[str, re.Pattern[str]]]:
    seed = ROOT / "dbt" / "seeds" / name
    if not seed.exists():
        return []
    out = []
    with open(seed, newline="") as f:
        for r in csv.DictReader(f):
            v = (r.get(col) or "").strip()
            if v:
                out.append((v, re.compile(rf"\b{re.escape(v)}\b", re.I)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roles", default=str(DISCOVERY / "exa_pilot_roles.csv"))
    ap.add_argument("--labels", default=str(DISCOVERY / "labels_exa.tsv"))
    ap.add_argument("--out", default=str(DISCOVERY / "exa_pilot_descriptions.csv"))
    ap.add_argument("--labelled-only", action="store_true",
                    help="fetch only boards carrying a labelled role (faster)")
    args = ap.parse_args()

    with open(args.roles, newline="") as f:
        roles = list(csv.DictReader(f))
    labels = A.load_labels(pathlib.Path(args.labels)) if pathlib.Path(args.labels).exists() else {}
    if args.labelled_only:
        want = {A.norm(r["url"]) for r in roles} & set(labels)
        keep = {(r["source"], r["board_ref"]) for r in roles if A.norm(r["url"]) in want}
        roles = [r for r in roles if (r["source"], r["board_ref"]) in keep]

    deal = load_seed("deal_breaker_tech.csv", "tech")
    tech = load_seed("desired_tech.csv", "tech")
    if not deal or not tech:
        print("missing dbt/seeds/deal_breaker_tech.csv or desired_tech.csv", file=sys.stderr)
        return 2
    print(f"{len(roles)} roles | {len(labels)} labels | "
          f"{len(deal)} deal-breakers | {len(tech)} desired-tech terms\n")

    boards = collections.defaultdict(list)
    for r in roles:
        boards[(r["source"], r["board_ref"])].append(r)

    out_rows, no_desc = [], 0
    for i, ((source, ref), group) in enumerate(sorted(boards.items()), start=1):
        if source not in DESC_APIS:
            no_desc += len(group)
            continue
        tmpl, key = DESC_APIS[source]
        try:
            time.sleep(0.3)
            resp = requests.get(tmpl.format(t=ref), headers={"User-Agent": UA}, timeout=30)
            payload = resp.json() if resp.status_code == 200 else None
        except Exception:  # noqa: BLE001 - a probe: every outcome is data
            payload = None
        jobs = payload if key is None else (payload.get(key) if isinstance(payload, dict) else None)
        by_url: dict[str, dict] = {}
        if isinstance(jobs, list):
            for j in jobs:
                if not isinstance(j, dict):
                    continue
                for k in ("absolute_url", "hostedUrl", "jobUrl", "url", "careers_url"):
                    v = j.get(k)
                    if isinstance(v, str) and v.strip():
                        by_url[A.norm(v)] = j
                        break
        for r in group:
            job = by_url.get(A.norm(r["url"]))
            desc = description_text(job) if job else ""
            if not desc:
                no_desc += 1
            dealt = [t for t, p in deal if p.search(desc)]
            techs = [t for t, p in tech if p.search(desc)]
            out_rows.append({
                **{k: r[k] for k in ("board_ref", "source", "title", "location", "url")},
                "label": labels.get(A.norm(r["url"]), ("", ""))[0],
                "why": labels.get(A.norm(r["url"]), ("", ""))[1],
                "desc_chars": len(desc),
                "deal_breakers": "|".join(dealt[:6]),
                "n_deal": len(dealt),
                "desired_tech": "|".join(techs[:10]),
                "n_tech": len(techs),
            })
        if i % 20 == 0:
            print(f"  ...{i}/{len(boards)} boards", flush=True)

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0]))
        w.writeheader()
        w.writerows(out_rows)
    print(f"\n{len(out_rows)} roles written -> {args.out}")
    print(f"  {no_desc} without a usable description "
          f"(source needs a per-posting detail call, or the posting closed)")

    scored = [r for r in out_rows if r["label"] in ("yes", "maybe", "no") and r["desc_chars"]]
    if not scored:
        print("\nno labelled roles with a description yet -- nothing to compare")
        return 0
    print(f"\n--- seeds vs {len(scored)} labelled roles with descriptions ---")
    print(f"  {'label':<8}{'n':>4}{'avg desired_tech':>19}{'avg deal_breakers':>20}"
          f"{'% with any deal-breaker':>26}")
    for lab in ("yes", "maybe", "no"):
        sel = [r for r in scored if r["label"] == lab]
        if not sel:
            continue
        n = len(sel)
        print(f"  {lab:<8}{n:>4}{sum(r['n_tech'] for r in sel)/n:>19.1f}"
              f"{sum(r['n_deal'] for r in sel)/n:>20.1f}"
              f"{sum(1 for r in sel if r['n_deal'])/n:>25.0%}")
    print("\n  most common deal-breakers on roles labelled `no`:")
    for t, c in collections.Counter(
            t for r in scored if r["label"] == "no"
            for t in filter(None, r["deal_breakers"].split("|"))).most_common(10):
        print(f"    {c:>3}  {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
