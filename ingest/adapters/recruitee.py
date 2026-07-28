"""Recruitee careers API adapter (public, no auth).

GET https://{board_ref}.recruitee.com/api/offers/ -> {"offers": [...]}
One subdomain = one company; a single response, no pagination. The body is split
across `description` and `requirements`, both already real HTML, so they are
concatenated here (as in ingest/adapters/lever.py) rather than left for dbt to
reassemble. The URL template is owned by the source registry (ingest/sources.py).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import requests

from shared.http import FetchPolicy
from shared.models import RawPosting


class RecruiteeAdapter:
    source = "recruitee"

    def __init__(self, url_template: str, policy: FetchPolicy | None = None) -> None:
        self.url_template = url_template
        self.policy = policy or FetchPolicy()

    def fetch(self, session: requests.Session, board_ref: str) -> list[RawPosting]:
        data = self.policy.get_json(session, self.url_template.format(board_ref=board_ref))
        # Strict: a response without "offers" is schema drift or an error body,
        # not an empty board - raise (per-company warn) instead of landing 0 rows.
        offers: list[dict[str, Any]] = data["offers"]
        return [self._map(item, board_ref) for item in offers]

    def _map(self, item: dict[str, Any], board_ref: str) -> RawPosting:
        return RawPosting(
            source=self.source,
            company=board_ref,
            external_id=str(item["id"]),
            title=item["title"],
            # Recruitee pre-formats "City, Province, Country"; the parts are also
            # present separately, so fall back to those if it ever stops.
            location=item.get("location") or _location_parts(item),
            remote_policy=_remote_policy(item),
            department=item.get("department"),
            employment_type=item.get("employment_type_code"),
            url=item["careers_url"],
            description_html=_assemble_body(item),
            posted_or_updated_at=_parse_dt(item.get("published_at")),
            raw=item,
        )


def _location_parts(item: dict[str, Any]) -> str | None:
    parts = [
        item.get("city"),
        item.get("state_name") or item.get("state_code"),
        item.get("country"),
    ]
    return ", ".join(p for p in parts if p) or None


def _remote_policy(item: dict[str, Any]) -> str | None:
    """Recruitee carries three independent booleans rather than one enum."""
    if item.get("remote"):
        return "Remote"
    if item.get("hybrid"):
        return "Hybrid"
    if item.get("on_site"):
        return "OnSite"
    return None


def _assemble_body(item: dict[str, Any]) -> str:
    parts = [item.get("description", ""), item.get("requirements", "")]
    return "\n".join(p for p in parts if p)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    # Recruitee emits "2026-07-22 14:20:52 UTC" — not ISO 8601, so the trailing
    # zone label is swapped for an offset fromisoformat understands.
    normalized = value.strip()
    if normalized.endswith(" UTC"):
        normalized = normalized.removesuffix(" UTC") + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
