"""HTTP helpers: a polite session with retries, a custom UA, and rate limiting.

Boards are fetched concurrently (ingest/pipeline.py), so politeness can no longer
be a global sleep between requests: that would serialize every board again. The
unit of politeness is the *host* -- `HostRateLimiter` keeps consecutive requests
to one host at least `min_interval_s` apart while letting different hosts run in
parallel. That distinction is what makes the parallel fetch worth doing: several
ATS put every company on its own subdomain ({ref}.bamboohr.com), so those boards
share no rate-limit budget at all, while Greenhouse/Lever/Ashby stay exactly as
polite per host as the old sequential sleep made them.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# (connect, read). A stalled connect fails fast and is retried instead of
# blocking the full read budget on an unreachable board.
DEFAULT_TIMEOUT = (10.0, 30.0)


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


class SessionPool:
    """One `requests.Session` per thread, all sharing the same UA and retries.

    `requests.Session` is not documented as thread-safe (its connection pool and
    cookie jar are shared mutable state), so worker threads must not share one.
    Sessions are kept per thread rather than per request so connection reuse —
    the reason a session is used at all — survives across boards.
    """

    def __init__(self, user_agent: str) -> None:
        self.user_agent = user_agent
        self._local = threading.local()

    def get(self) -> requests.Session:
        session: requests.Session | None = getattr(self._local, "session", None)
        if session is None:
            session = build_session(self.user_agent)
            self._local.session = session
        return session


class HostRateLimiter:
    """Keep requests to any one host `min_interval_s` apart, hosts independent.

    Each call reserves the next free slot for its host under the lock and then
    sleeps outside it, so a thread waiting on a slow host never blocks a thread
    aiming at a different one. Reserving (rather than sleeping then stamping)
    also means N threads targeting the same host queue up at the interval
    instead of all reading the same stale timestamp and firing at once.
    """

    def __init__(self, min_interval_s: float) -> None:
        self.min_interval_s = min_interval_s
        self._next_allowed: dict[str, float] = {}
        self._lock = threading.Lock()

    def acquire(self, url: str, *, min_interval_s: float | None = None) -> None:
        interval = self.min_interval_s if min_interval_s is None else min_interval_s
        if interval <= 0:
            return
        host = urlsplit(url).netloc
        now = time.monotonic()
        with self._lock:
            slot = max(now, self._next_allowed.get(host, 0.0))
            self._next_allowed[host] = slot + interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)


def get_json(
    session: requests.Session,
    url: str,
    *,
    min_interval_s: float = 0.5,
    limiter: HostRateLimiter | None = None,
    timeout: tuple[float, float] = DEFAULT_TIMEOUT,
) -> Any:
    """GET a URL and return parsed JSON, pausing first to respect rate limits.

    With a `limiter`, the pause is per host and shared across threads; without
    one, it degrades to the original unconditional sleep so a single-threaded
    caller (a test, a one-off script) behaves exactly as before.
    """
    if limiter is not None:
        limiter.acquire(url, min_interval_s=min_interval_s)
    else:
        time.sleep(min_interval_s)
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()
