"""Ashby public job-posting API adapter (public, no auth).

GET https://api.ashbyhq.com/posting-api/job-board/{board_ref}?includeCompensation=true
One job-board name (the `jobs.ashbyhq.com/<board_ref>` slug) = one company. The
response is a single `{"jobs": [...]}` object with no pagination, so this mirrors
Greenhouse/Lever: one GET, map each item into the common schema. `descriptionHtml`
is already real HTML (not entity-escaped like Greenhouse's `content`).
The URL template is owned by the source registry (ingest/sources.py).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import requests

from shared.http import get_json
from shared.models import RawPosting


class AshbyAdapter:
    source = "ashby"

    def __init__(self, url_template: str) -> None:
        self.url_template = url_template

    def fetch(self, session: requests.Session, board_ref: str) -> list[RawPosting]:
        data = get_json(session, self.url_template.format(board_ref=board_ref))
        # Strict: a response without "jobs" is schema drift or an error body, not
        # an empty board - raise (per-company warn) instead of landing 0 rows.
        jobs: list[dict[str, Any]] = data["jobs"]
        return [self._map(item, board_ref) for item in jobs]

    def _map(self, item: dict[str, Any], board_ref: str) -> RawPosting:
        return RawPosting(
            source=self.source,
            company=board_ref,
            external_id=str(item["id"]),
            title=item["title"],
            location=item.get("location"),
            remote_policy=item.get("workplaceType"),  # "OnSite" | "Remote" | "Hybrid"
            department=item.get("department"),
            employment_type=item.get("employmentType"),
            url=item["jobUrl"],
            description_html=item.get("descriptionHtml", ""),
            posted_or_updated_at=_parse_dt(item.get("publishedAt")),
            raw=item,
        )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    # Ashby emits ISO 8601 with an explicit offset (…+00:00); tolerate a "Z" too.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
