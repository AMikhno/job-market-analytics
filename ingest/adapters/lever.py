"""Lever Postings API adapter (public, no auth).

GET https://api.lever.co/v0/postings/{site}?mode=json
The body is split across description + lists[] + additional; we concatenate it
here (in tested Python) so dbt never has to flatten a JSON array cross-dialect.
The URL template is owned by the source registry (ingest/sources.py).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import requests

from shared.http import get_json
from shared.models import RawPosting


class LeverAdapter:
    source = "lever"

    def __init__(self, url_template: str) -> None:
        self.url_template = url_template

    def fetch(self, session: requests.Session, board_ref: str) -> list[RawPosting]:
        items: list[dict[str, Any]] = get_json(
            session, self.url_template.format(board_ref=board_ref)
        )
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
