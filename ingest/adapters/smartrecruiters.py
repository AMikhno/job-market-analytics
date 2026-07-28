"""SmartRecruiters postings API adapter (public, no auth).

GET https://api.smartrecruiters.com/v1/companies/{board_ref}/postings?limit=100&offset=N
    -> {"offset": N, "limit": 100, "totalFound": M, "content": [...]}
GET https://api.smartrecruiters.com/v1/companies/{board_ref}/postings/{id}
    -> {..., "jobAd": {"sections": {"jobDescription": {"text": …}, …}}}

The only Tier 1 platform that paginates: the list caps at 100 and reports
`totalFound`, so pages are walked until every posting is in hand. The list also
carries no description, so each posting costs one detail GET (ADR-0021) — which
also supplies the public `postingUrl`; the list only has an API `ref`.

Every company shares one host here, so the per-host limiter paces this board's
calls end to end. A large board is therefore slow by design rather than rude.

`companyDescription` is deliberately left out of the mapped body: it is the same
blurb on every posting, so it would tell the keyword filters nothing about the
job. The URL templates are owned by the source registry (ingest/sources.py).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import requests

from shared.http import FetchPolicy
from shared.models import RawPosting

# Job-specific sections, in reading order. companyDescription is excluded.
_SECTIONS = ("jobDescription", "qualifications", "additionalInformation")
_EMPTY_PARTS = re.compile(r"(,\s*)+,")


class SmartRecruitersAdapter:
    source = "smartrecruiters"

    def __init__(
        self,
        url_template: str,
        detail_url_template: str,
        policy: FetchPolicy | None = None,
        page_size: int = 100,
    ) -> None:
        self.url_template = url_template
        self.detail_url_template = detail_url_template
        self.policy = policy or FetchPolicy()
        self.page_size = page_size

    def fetch(self, session: requests.Session, board_ref: str) -> list[RawPosting]:
        listings = self._list(session, board_ref)
        return [
            self._map(item, self._detail(session, board_ref, item), board_ref) for item in listings
        ]

    def _list(self, session: requests.Session, board_ref: str) -> list[dict[str, Any]]:
        """Walk the paginated list until `totalFound` postings have been seen.

        The loop is bounded by the payload's own count *and* by a page coming
        back empty, so a board that misreports totalFound (or keeps returning
        the same page) ends the walk instead of spinning forever.
        """
        collected: list[dict[str, Any]] = []
        offset = 0
        while True:
            url = self.url_template.format(board_ref=board_ref, limit=self.page_size, offset=offset)
            payload = self.policy.get_json(session, url)
            # Strict: a response without "content" is schema drift or an error
            # body - raise (per-company warn) instead of landing 0 rows.
            page: list[dict[str, Any]] = payload["content"]
            if not page:
                break
            collected.extend(page)
            total = payload.get("totalFound")
            if total is None or len(collected) >= int(total):
                break
            offset += len(page)
        return collected

    def _detail(
        self, session: requests.Session, board_ref: str, item: dict[str, Any]
    ) -> dict[str, Any]:
        url = self.detail_url_template.format(board_ref=board_ref, posting_id=item["id"])
        detail: dict[str, Any] = self.policy.get_json(session, url)
        return detail

    def _map(self, item: dict[str, Any], detail: dict[str, Any], board_ref: str) -> RawPosting:
        location = item.get("location") or detail.get("location") or {}
        return RawPosting(
            source=self.source,
            company=board_ref,
            external_id=str(item["id"]),
            title=item["name"],
            location=_location(location),
            remote_policy=_remote_policy(location),
            department=(item.get("department") or {}).get("label"),
            employment_type=(item.get("typeOfEmployment") or {}).get("label"),
            # The list's `ref` is an API URL; only the detail has the public one.
            url=detail["postingUrl"],
            description_html=_assemble_body(detail),
            posted_or_updated_at=_parse_dt(item.get("releasedDate")),
            raw={"listing": item, "detail": detail},
        )


def _location(location: dict[str, Any]) -> str | None:
    """Prefer the preformatted `fullLocation`, tidied.

    It spells the country out ("Munich, , Germany") where the structured fields
    hold a two-letter code, but it leaves an empty slot for a missing region —
    those collapse here rather than landing a doubled comma.
    """
    full = (location.get("fullLocation") or "").strip()
    if full:
        return _EMPTY_PARTS.sub(",", full).strip(", ") or None
    parts = [location.get("city"), location.get("region"), location.get("country")]
    return ", ".join(p for p in parts if p) or None


def _remote_policy(location: dict[str, Any]) -> str | None:
    if location.get("remote"):
        return "Remote"
    if location.get("hybrid"):
        return "Hybrid"
    return None


def _assemble_body(detail: dict[str, Any]) -> str:
    sections = ((detail.get("jobAd") or {}).get("sections")) or {}
    parts: list[str] = []
    for name in _SECTIONS:
        section = sections.get(name) or {}
        text = section.get("text")
        if not text:
            continue
        title = section.get("title")
        parts.append(f"<h3>{title}</h3>{text}" if title else text)
    return "\n".join(parts)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
