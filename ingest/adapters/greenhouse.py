"""Greenhouse Job Board API adapter (public, no auth).

GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
One board token = one company. Only `updated_at` is exposed (no original post date).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import requests

from shared.http import get_json
from shared.models import RawPosting

URL_TEMPLATE = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


class GreenhouseAdapter:
    source = "greenhouse"

    def fetch(self, session: requests.Session, slug: str) -> list[RawPosting]:
        data = get_json(session, URL_TEMPLATE.format(slug=slug))
        jobs: list[dict[str, Any]] = data.get("jobs", [])
        return [self._map(item, slug) for item in jobs]

    def _map(self, item: dict[str, Any], slug: str) -> RawPosting:
        location = (item.get("location") or {}).get("name")
        return RawPosting(
            source=self.source,
            company=slug,
            external_id=str(item["id"]),
            title=item["title"],
            location=location,
            remote_policy=None,  # Greenhouse board API doesn't expose this in V1
            url=item["absolute_url"],
            description_html=item.get("content", ""),
            posted_or_updated_at=_parse_dt(item.get("updated_at")),
            raw=item,
        )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
