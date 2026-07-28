"""Rippling ATS board API adapter (public, no auth).

GET https://api.rippling.com/platform/api/ats/v1/board/{board_ref}/jobs
    -> [ {uuid, name, department, url, workLocation}, ... ]
GET .../board/{board_ref}/jobs/{uuid}
    -> {uuid, name, description: {company, role}, workLocations[], ...}

Two things make this more than a copy of the Ashby adapter:

1. **The list repeats a job once per location.** A five-city posting arrives as
   five entries sharing one `uuid`. Landed as-is they would collide on
   job_key (source+company+external_id), leaving silver to keep whichever row
   happened to be ingested last — i.e. an arbitrary one of the five locations.
   They are collapsed here instead, and the detail's `workLocations` names all
   the places the job is open.
2. **The list has no description**, and one is required for the keyword filters
   to mean anything (ADR-0021), so each unique job costs one detail GET.

Note the detail's `description` splits into `company` (a shared blurb) and
`role`; only `role` is mapped, for the same reason as Workable — a company
blurb repeated on every posting tells the filters nothing about the job.

The URL templates are owned by the source registry (ingest/sources.py).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import requests

from shared.http import FetchPolicy
from shared.models import RawPosting


class RipplingAdapter:
    source = "rippling"

    def __init__(
        self, url_template: str, detail_url_template: str, policy: FetchPolicy | None = None
    ) -> None:
        self.url_template = url_template
        self.detail_url_template = detail_url_template
        self.policy = policy or FetchPolicy()

    def fetch(self, session: requests.Session, board_ref: str) -> list[RawPosting]:
        items = self.policy.get_json(session, self.url_template.format(board_ref=board_ref))
        # Strict: Rippling returns a bare JSON array; a dict here is an error body
        # or schema drift - raise (per-company warn) instead of landing 0 rows.
        if not isinstance(items, list):
            raise ValueError(f"expected a JSON array from Rippling, got {type(items).__name__}")
        return [
            self._map(job, self._detail(session, board_ref, uuid), board_ref)
            for uuid, job in _collapse(items).items()
        ]

    def _detail(self, session: requests.Session, board_ref: str, uuid: str) -> dict[str, Any]:
        url = self.detail_url_template.format(board_ref=board_ref, job_uuid=uuid)
        detail: dict[str, Any] = self.policy.get_json(session, url)
        return detail

    def _map(self, job: _Job, detail: dict[str, Any], board_ref: str) -> RawPosting:
        description = detail.get("description") or {}
        return RawPosting(
            source=self.source,
            company=board_ref,
            external_id=job.uuid,
            title=job.name,
            location=_location(detail) or job.locations,
            remote_policy="Remote" if detail.get("isRemote") else None,
            department=(detail.get("department") or {}).get("name") or job.department,
            employment_type=(detail.get("employmentType") or {}).get("label"),
            url=job.url,
            # `role` only: `company` is the same blurb on every posting.
            description_html=description["role"],
            posted_or_updated_at=_parse_dt(detail.get("createdOn")),
            raw={"listing": job.items, "detail": detail},
        )


class _Job:
    """One logical posting, assembled from the list's per-location rows."""

    def __init__(self, uuid: str, items: list[dict[str, Any]]) -> None:
        self.uuid = uuid
        self.items = items
        first = items[0]
        self.name: str = first["name"]
        self.url: str = first["url"]
        self.department: str | None = (first.get("department") or {}).get("label")
        # Every location the list showed for this job, de-duplicated in order.
        labels = [(i.get("workLocation") or {}).get("label") for i in items]
        self.locations: str | None = "; ".join(dict.fromkeys(x for x in labels if x)) or None


def _collapse(items: list[dict[str, Any]]) -> dict[str, _Job]:
    """Group the list's rows by uuid, preserving board order."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(str(item["uuid"]), []).append(item)
    return {uuid: _Job(uuid, rows) for uuid, rows in grouped.items()}


def _location(detail: dict[str, Any]) -> str | None:
    locations = detail.get("workLocations") or []
    named = [loc for loc in locations if isinstance(loc, str) and loc]
    return "; ".join(dict.fromkeys(named)) or None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
