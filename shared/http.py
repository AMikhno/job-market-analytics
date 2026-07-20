"""HTTP helpers: a polite session with retries, a custom UA, and rate limiting."""

from __future__ import annotations

import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def build_session(user_agent: str) -> requests.Session:
    """Return a session with sane retry/backoff and a descriptive User-Agent.

    Retries cover both transient HTTP statuses (429/5xx) and connection-level
    faults (refused/reset/timeout): connect/read/status are set explicitly so a
    flaky board or an egress blip is retried rather than counting as a failed
    board — one bad request no longer sinks a small source. Jitter de-syncs the
    backoff when several boards hit the same host at once.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=1.0,
        backoff_jitter=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def get_json(session: requests.Session, url: str, *, min_interval_s: float = 0.5) -> Any:
    """GET a URL and return parsed JSON, pausing first to respect rate limits.

    The timeout is (connect, read): a stalled connect fails fast at 10s and is
    retried, instead of blocking the full read budget on an unreachable board.
    """
    time.sleep(min_interval_s)
    resp = session.get(url, timeout=(10, 30))
    resp.raise_for_status()
    return resp.json()
