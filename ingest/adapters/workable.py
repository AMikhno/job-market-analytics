"""Workable careers-widget API adapter (public, no auth).

GET https://apply.workable.com/api/v1/widget/accounts/{board_ref}?details=true
    -> {"name": …, "description": <company blurb>, "jobs": [...]}

This is the **v1 widget** endpoint. The `api/v3/accounts/{ref}/jobs` path that
appears in newer documentation returned 404 for every ref tried (see
docs/research/ats-feeds.md) — this one is what actually serves.

`details=true` is what puts each posting's HTML description in the list, so there
is no per-posting call. Note the top-level `description` is the *company*
blurb, not a job: mapping it would give every posting of a company identical
text and poison the keyword signals. The URL template is owned by the source
registry (ingest/sources.py).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import requests

from shared.http import FetchPolicy
from shared.models import RawPosting


class WorkableAdapter:
    source = "workable"

    def __init__(self, url_template: str, policy: FetchPolicy | None = None) -> None:
        self.url_template = url_template
        self.policy = policy or FetchPolicy()

    def fetch(self, session: requests.Session, board_ref: str) -> list[RawPosting]:
        data = self.policy.get_json(session, self.url_template.format(board_ref=board_ref))
        # Strict: a response without "jobs" is schema drift or an error body, not
        # an empty board - raise (per-company warn) instead of landing 0 rows.
        jobs: list[dict[str, Any]] = data["jobs"]
        return [self._map(item, board_ref) for item in jobs]

    def _map(self, item: dict[str, Any], board_ref: str) -> RawPosting:
        return RawPosting(
            source=self.source,
            company=board_ref,
            # `shortcode` is the id Workable's own URLs use (apply.workable.com/j/<shortcode>).
            external_id=str(item["shortcode"]),
            title=item["title"],
            location=_location(item),
            remote_policy="Remote" if item.get("telecommuting") else None,
            # Workable writes an unset field as "" rather than null (seen on a
            # live board), which would land as an empty string instead of NULL.
            department=item.get("department") or None,
            employment_type=item.get("employment_type") or None,
            url=item.get("url") or item["shortlink"],
            # The *job's* description; the account-level one is a company blurb.
            description_html=item.get("description", ""),
            posted_or_updated_at=_parse_date(item.get("published_on") or item.get("created_at")),
            raw=item,
        )


def _location(item: dict[str, Any]) -> str | None:
    parts = [item.get("city"), item.get("state"), item.get("country")]
    return ", ".join(p for p in parts if p) or None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    # Workable emits a bare date ("2026-07-15"); anchor it to UTC midnight so
    # every source's timestamp is comparable and tz-aware.
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
