"""Greenhouse Job Board API adapter (public, no auth).

GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
One board token = one company. Only `updated_at` is exposed (no original post date).
The URL template is owned by the source registry (ingest/sources.py).
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

import requests

from shared.http import get_json
from shared.models import RawPosting


class GreenhouseAdapter:
    source = "greenhouse"

    def __init__(self, url_template: str) -> None:
        self.url_template = url_template

    def fetch(self, session: requests.Session, board_ref: str) -> list[RawPosting]:
        data = get_json(session, self.url_template.format(board_ref=board_ref))
        # Strict: a response without "jobs" is schema drift or an error body, not
        # an empty board - raise (per-company warn) instead of landing 0 rows.
        jobs: list[dict[str, Any]] = data["jobs"]
        return [self._map(item, board_ref) for item in jobs]

    def _map(self, item: dict[str, Any], board_ref: str) -> RawPosting:
        location = (item.get("location") or {}).get("name")
        return RawPosting(
            source=self.source,
            company=board_ref,
            external_id=str(item["id"]),
            title=item["title"],
            location=location,
            remote_policy=None,  # Greenhouse board API doesn't expose this in V1
            url=item["absolute_url"],
            # The boards API returns `content` HTML-escaped (&lt;p&gt;…); unescape once
            # so description_html is real HTML like every other source.
            description_html=html.unescape(item.get("content", "")),
            posted_or_updated_at=_parse_dt(item.get("updated_at")),
            raw=item,
        )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
