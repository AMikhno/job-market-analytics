"""Lever Postings API adapter (public, no auth).

GET https://api.lever.co/v0/postings/{site}?mode=json
The body is split across description + lists[] + additional; we concatenate it
here (in tested Python) so dbt never has to flatten a JSON array cross-dialect.
The URL templates are owned by the source registry (ingest/sources.py).

Lever shards some boards onto an EU host (api.eu.lever.co) and the US host
returns 404 for those -- with nothing in the ref to say which. Region is not a
property of the *company* (an NA employer can sit on an EU board), so it is not
in the company list: the adapter tries US, then EU, and reports the shard it
used. Both 404 means the board really is gone, and that still raises.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import requests

from shared.http import get_json
from shared.models import RawPosting
from shared.redact import redact_ref

log = logging.getLogger("ingest")


class LeverAdapter:
    source = "lever"

    def __init__(self, url_template: str, eu_url_template: str | None = None) -> None:
        self.url_template = url_template
        self.eu_url_template = eu_url_template

    def _get(self, session: requests.Session, board_ref: str) -> Any:
        """Fetch from the US shard, falling back to the EU shard on a 404.

        The 404 catch is deliberate and narrow: only a 404 (board not on this
        shard) is retried elsewhere, only when an EU template is configured, and
        the EU attempt's own failure propagates. Any other status still raises
        from the first call, so a real outage is never masked as a missing board.
        """
        try:
            return get_json(session, self.url_template.format(board_ref=board_ref))
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status != 404 or not self.eu_url_template:
                raise
        payload = get_json(session, self.eu_url_template.format(board_ref=board_ref))
        # Always redacted: this repo's CI logs are public, and unlike the
        # pipeline's own log lines there is no Settings here to consult.
        log.info("lever board_ref=%s served by the EU shard", redact_ref(board_ref))
        return payload

    def fetch(self, session: requests.Session, board_ref: str) -> list[RawPosting]:
        items: list[dict[str, Any]] = self._get(session, board_ref)
        # Strict: Lever returns a bare JSON array; a dict here is an error body
        # or schema drift - raise (per-company warn) instead of landing 0 rows.
        if not isinstance(items, list):
            raise ValueError(f"expected a JSON array from Lever, got {type(items).__name__}")
        return [self._map(item, board_ref) for item in items]

    def _map(self, item: dict[str, Any], board_ref: str) -> RawPosting:
        cats = item.get("categories") or {}
        return RawPosting(
            source=self.source,
            company=board_ref,
            external_id=str(item["id"]),
            title=item["text"],
            location=cats.get("location"),
            remote_policy=item.get("workplaceType"),
            department=cats.get("department"),
            employment_type=cats.get("commitment"),
            url=item["hostedUrl"],
            description_html=_assemble_body(item),
            posted_or_updated_at=_parse_epoch_ms(item.get("createdAt")),
            raw=item,
        )


def _assemble_body(item: dict[str, Any]) -> str:
    parts: list[str] = [item.get("description", "")]
    for block in item.get("lists", []):
        parts.append(f"<h3>{block.get('text', '')}</h3>{block.get('content', '')}")
    parts.append(item.get("additional", ""))
    return "\n".join(p for p in parts if p)


def _parse_epoch_ms(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)
