"""HTTP helpers: a polite session with retries, a custom UA, and rate limiting."""

from __future__ import annotations

import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def build_session(user_agent: str) -> requests.Session:
    """Return a session with sane retry/backoff and a descriptive User-Agent."""
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    retry = Retry(
        total=4,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def get_json(session: requests.Session, url: str, *, min_interval_s: float = 0.5) -> Any:
    """GET a URL and return parsed JSON, pausing first to respect rate limits."""
    time.sleep(min_interval_s)
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()
