"""Adapter protocol: one adapter per access method."""

from __future__ import annotations

from typing import Protocol

import requests

from shared.models import RawPosting


class SourceAdapter(Protocol):
    """Fetch postings for one company slug and return them in the common schema."""

    source: str

    def fetch(self, session: requests.Session, slug: str) -> list[RawPosting]: ...
