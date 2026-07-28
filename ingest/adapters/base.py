"""Adapter protocol: one adapter per access method."""

from __future__ import annotations

from typing import Protocol

import requests

from shared.http import FetchPolicy
from shared.models import RawPosting


class SourceAdapter(Protocol):
    """Fetch postings for one company's board and return the common schema.

    `board_ref` is the ATS-specific path fragment from the company list; each
    adapter owns its interpretation (bare token for Greenhouse/Lever/Ashby, richer
    multi-segment forms for future ATS like Workday).

    `fetch` is called from a worker thread (one board per call, several boards at
    once), so an adapter must keep no per-board state on itself and must use the
    session it is handed rather than one of its own.
    """

    source: str
    url_template: str
    policy: FetchPolicy

    def fetch(self, session: requests.Session, board_ref: str) -> list[RawPosting]: ...
