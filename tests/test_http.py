"""HTTP session: retry policy, the split connect/read timeout, rate limiting."""

import threading

import pytest
import requests
import responses

from shared.http import HostRateLimiter, SessionPool, build_session, get_json


def test_session_retry_policy_covers_connection_faults() -> None:
    """connect/read/status are retried (not just status), so a reset/refused
    board is retried rather than counted as a failure; jitter de-syncs backoff."""
    session = build_session("test-agent/1.0")
    retry = session.get_adapter("https://api.example.test").max_retries
    assert retry.total == 5
    assert retry.connect == 5 and retry.read == 5 and retry.status == 5
    assert retry.backoff_jitter == 1.0
    assert {429, 500, 502, 503, 504} <= set(retry.status_forcelist)
    assert session.headers["User-Agent"] == "test-agent/1.0"


@responses.activate
def test_retries_transient_5xx_then_succeeds() -> None:
    url = "https://api.example.test/board"
    responses.add(responses.GET, url, status=503)  # transient blip
    responses.add(responses.GET, url, json={"jobs": []}, status=200)

    session = build_session("test-agent/1.0")
    assert get_json(session, url, min_interval_s=0) == {"jobs": []}
    assert len(responses.calls) == 2  # retried once after the 503


@responses.activate
def test_gives_up_after_persistent_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    # No real backoff sleeps in the test; the retry *count* is what we assert.
    monkeypatch.setattr("urllib3.util.retry.Retry.sleep", lambda self, response=None: None)
    url = "https://api.example.test/dead"
    responses.add(responses.GET, url, status=503)  # always failing

    session = build_session("test-agent/1.0")
    with pytest.raises(requests.exceptions.RequestException):
        get_json(session, url, min_interval_s=0)
    assert len(responses.calls) == 6  # initial attempt + 5 retries


class _Resp:
    def raise_for_status(self) -> None: ...
    def json(self) -> dict[str, int]:
        return {"ok": 1}


class _Session:
    """Records what get_json passed through, without touching the network."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, timeout: object = None) -> _Resp:
        self.calls.append({"url": url, "timeout": timeout})
        return _Resp()


def test_get_json_uses_split_connect_read_timeout() -> None:
    """A stalled connect must fail fast (10s) and be retried, not block on the
    full read budget — so the timeout is passed as a (connect, read) tuple."""
    session = _Session()

    assert get_json(session, "https://api.example.test/x", min_interval_s=0) == {"ok": 1}
    assert session.calls[0]["timeout"] == (10.0, 30.0)


def test_get_json_accepts_a_per_source_timeout() -> None:
    """A source with a heavier response (paginated, detail-fetched) can widen the
    read budget without changing it for every other board."""
    session = _Session()

    get_json(session, "https://api.example.test/x", min_interval_s=0, timeout=(5.0, 60.0))

    assert session.calls[0]["timeout"] == (5.0, 60.0)


class _FakeClock:
    """Monotonic clock + sleep that advances it, so interval assertions are exact
    and the suite never actually waits."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    fake = _FakeClock()
    monkeypatch.setattr("shared.http.time.monotonic", fake.monotonic)
    monkeypatch.setattr("shared.http.time.sleep", fake.sleep)
    return fake


def test_limiter_spaces_out_requests_to_one_host(clock: _FakeClock) -> None:
    limiter = HostRateLimiter(0.5)

    for _ in range(3):
        limiter.acquire("https://boards.example.test/a")

    # first call is free; each subsequent one waits a full interval
    assert clock.slept == [0.5, 0.5]


def test_limiter_lets_different_hosts_run_without_waiting(clock: _FakeClock) -> None:
    """The point of per-host limiting: boards on their own subdomain (BambooHR,
    Recruitee, Pinpoint) never queue behind each other."""
    limiter = HostRateLimiter(0.5)

    limiter.acquire("https://one.example.test/jobs")
    limiter.acquire("https://two.example.test/jobs")
    limiter.acquire("https://three.example.test/jobs")

    assert clock.slept == []


def test_limiter_honors_a_per_call_interval(clock: _FakeClock) -> None:
    limiter = HostRateLimiter(0.5)

    limiter.acquire("https://one.example.test/jobs", min_interval_s=2.0)
    limiter.acquire("https://one.example.test/jobs", min_interval_s=2.0)

    assert clock.slept == [2.0]


def test_limiter_reserves_slots_so_concurrent_callers_queue(clock: _FakeClock) -> None:
    """Threads must not all read the same "last request" timestamp and fire
    together — the slot is reserved under the lock before sleeping."""
    limiter = HostRateLimiter(1.0)
    limiter.acquire("https://one.example.test/a")  # takes t=1000, reserves 1001

    # a second and third caller arriving at the same instant get 1001 and 1002
    assert limiter._next_allowed["one.example.test"] == 1001.0
    limiter.acquire("https://one.example.test/b")
    assert limiter._next_allowed["one.example.test"] == 1002.0


def test_limiter_disabled_by_a_zero_interval(clock: _FakeClock) -> None:
    HostRateLimiter(0).acquire("https://one.example.test/a")

    assert clock.slept == []


def test_get_json_uses_the_limiter_instead_of_a_blanket_sleep(clock: _FakeClock) -> None:
    session = _Session()
    limiter = HostRateLimiter(0.5)

    get_json(session, "https://api.example.test/x", limiter=limiter)
    get_json(session, "https://api.example.test/y", limiter=limiter)
    get_json(session, "https://other.example.test/z", limiter=limiter)

    # only the second call to the *same* host waited
    assert clock.slept == [0.5]


def test_session_pool_gives_each_thread_its_own_session() -> None:
    """requests.Session is not thread-safe, so workers must not share one."""
    pool = SessionPool("test-agent/1.0")
    # keep the objects, not their ids: a dead thread's session can be collected
    # and its id handed to the next one, which would fake a passing test
    seen: list[requests.Session] = []
    lock = threading.Lock()

    def grab() -> None:
        session = pool.get()
        with lock:
            seen.append(session)

    threads = [threading.Thread(target=grab) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len({id(s) for s in seen}) == 4  # four threads, four sessions
    assert pool.get() is pool.get()  # ...but one per thread, reused
    assert pool.get().headers["User-Agent"] == "test-agent/1.0"
