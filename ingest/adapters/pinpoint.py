"""Pinpoint postings API adapter (public, no auth).

GET https://{board_ref}.pinpointhq.com/postings.json -> {"data": [...]}
One subdomain = one company; a single response, no pagination.

Pinpoint splits a posting across several labelled HTML blocks (description, key
responsibilities, skills, benefits) with the headings carried alongside as
separate fields. They are reassembled here — the keyword filters read one text
column, and dropping the extra blocks would hide most of a posting's tech terms.
The URL template is owned by the source registry (ingest/sources.py).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import requests

from shared.http import FetchPolicy
from shared.models import RawPosting

# (heading field, body field). Rendered in this order, after the description.
_SECTIONS = (
    ("key_responsibilities_header", "key_responsibilities"),
    ("skills_knowledge_expertise_header", "skills_knowledge_expertise"),
    ("benefits_header", "benefits"),
)


class PinpointAdapter:
    source = "pinpoint"

    def __init__(self, url_template: str, policy: FetchPolicy | None = None) -> None:
        self.url_template = url_template
        self.policy = policy or FetchPolicy()

    def fetch(self, session: requests.Session, board_ref: str) -> list[RawPosting]:
        data = self.policy.get_json(session, self.url_template.format(board_ref=board_ref))
        # Strict: a response without "data" is schema drift or an error body, not
        # an empty board - raise (per-company warn) instead of landing 0 rows.
        postings: list[dict[str, Any]] = data["data"]
        return [self._map(item, board_ref) for item in postings]

    def _map(self, item: dict[str, Any], board_ref: str) -> RawPosting:
        return RawPosting(
            source=self.source,
            company=board_ref,
            external_id=str(item["id"]),
            title=item["title"],
            location=_location(item),
            remote_policy=item.get("workplace_type_text"),  # "Remote" | "Hybrid" | "On Site"
            department=(item.get("department") or {}).get("name"),
            employment_type=item.get("employment_type_text"),
            url=item["url"],
            description_html=_assemble_body(item),
            # Pinpoint's postings.json carries no post/update date (only
            # `deadline_at`), so this is normally None: gold sorts nulls last and
            # the digest's "new since last run" runs off first_seen_at, which is
            # ours. Read opportunistically in case a board ever supplies one --
            # inventing a date from the deadline would be worse than no date.
            posted_or_updated_at=_parse_dt(item.get("created_at") or item.get("published_at")),
            raw=item,
        )


def _location(item: dict[str, Any]) -> str | None:
    loc = item.get("location") or {}
    parts = [loc.get("city"), loc.get("province")]
    # `name` is the board's own label ("AUS - Brisbane"); it is the fallback
    # rather than the primary, since it is not a normalized place name.
    return ", ".join(p for p in parts if p) or loc.get("name") or None


def _assemble_body(item: dict[str, Any]) -> str:
    parts = [item.get("description") or ""]
    for header_field, body_field in _SECTIONS:
        body = item.get(body_field)
        if not body:
            continue
        header = item.get(header_field)
        parts.append(f"<h3>{header}</h3>{body}" if header else body)
    return "\n".join(p for p in parts if p)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
