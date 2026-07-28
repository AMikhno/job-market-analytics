"""BambooHR careers API adapter (public, no auth).

GET https://{board_ref}.bamboohr.com/careers/list        -> {"meta": …, "result": [...]}
GET https://{board_ref}.bamboohr.com/careers/{id}/detail -> {"result": {"jobOpening": {...}}}

Two calls, unlike Greenhouse/Lever/Ashby: the list carries only id, title,
department, employment status and a location — **no description, no URL and no
posted date**. Descriptions are not optional here, because silver's deal-breaker
filter and desired-tech signals run on the posting text (ADR-0021): a board
landed from the list alone would be permanently unfilterable. So each posting
costs one extra GET, which also supplies the canonical share URL and the date.

The two requests go to the same host, so the per-host limiter (shared/http.py)
paces them exactly as it paces two boards — and because every BambooHR company
has its own subdomain, different companies still run fully in parallel.

The URL templates are owned by the source registry (ingest/sources.py).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import requests

from shared.http import FetchPolicy
from shared.models import RawPosting


class BambooHRAdapter:
    source = "bamboohr"

    def __init__(
        self, url_template: str, detail_url_template: str, policy: FetchPolicy | None = None
    ) -> None:
        self.url_template = url_template
        self.detail_url_template = detail_url_template
        self.policy = policy or FetchPolicy()

    def fetch(self, session: requests.Session, board_ref: str) -> list[RawPosting]:
        data = self.policy.get_json(session, self.url_template.format(board_ref=board_ref))
        # Strict: a response without "result" is schema drift or an error body,
        # not an empty board - raise (per-company warn) instead of landing 0 rows.
        listings: list[dict[str, Any]] = data["result"]
        return [
            self._map(item, self._detail(session, board_ref, item), board_ref) for item in listings
        ]

    def _detail(
        self, session: requests.Session, board_ref: str, item: dict[str, Any]
    ) -> dict[str, Any]:
        url = self.detail_url_template.format(board_ref=board_ref, job_id=item["id"])
        payload = self.policy.get_json(session, url)
        opening: dict[str, Any] = payload["result"]["jobOpening"]
        return opening

    def _map(self, item: dict[str, Any], detail: dict[str, Any], board_ref: str) -> RawPosting:
        job_id = str(item["id"])
        return RawPosting(
            source=self.source,
            company=board_ref,
            external_id=job_id,
            title=item["jobOpeningName"],
            # Both payloads carry a location and either can be half-filled, so
            # they are coalesced part by part rather than one preferred whole.
            location=_location(item, detail),
            # The list exposes `isRemote`; `locationType` is an unlabelled code
            # (values seen: "1"), so it is deliberately not interpreted here --
            # it stays in `raw` for V2 rather than being guessed at.
            remote_policy="Remote" if item.get("isRemote") else None,
            department=item.get("departmentLabel"),
            employment_type=item.get("employmentStatusLabel"),
            # Only the detail response carries a URL; the careers path is a stable
            # fallback if a board ever omits the share link.
            url=detail.get("jobOpeningShareUrl")
            or f"https://{board_ref}.bamboohr.com/careers/{job_id}",
            description_html=detail["description"],
            posted_or_updated_at=_parse_date(detail.get("datePosted")),
            # Both payloads, so nothing fetched is thrown away (compensation and
            # minimumExperience live only on the detail).
            raw={**item, "detail": detail},
        )


def _location(*payloads: dict[str, Any]) -> str | None:
    """Human-readable location, coalesced across BambooHR's location objects.

    Each payload has an `atsLocation` (country/state/province/city) and a
    `location` (city/state), and both can be partly null — the list often knows
    only the country while the detail also names the city. So each part is taken
    from the first payload that supplies it. All null yields None, which silver
    treats as "unknown" and keeps.
    """
    objects = [
        obj for p in payloads for obj in (p.get("atsLocation") or {}, p.get("location") or {})
    ]

    def part(*keys: str) -> str | None:
        return next((obj[k] for obj in objects for k in keys if obj.get(k)), None)

    parts = [part("city"), part("state", "province"), part("country", "addressCountry")]
    return ", ".join(p for p in parts if p) or None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    # BambooHR emits a bare date ("2026-05-11"); anchor it to UTC midnight so
    # every source's timestamp is comparable and tz-aware.
    return datetime.fromisoformat(value).replace(tzinfo=UTC)
