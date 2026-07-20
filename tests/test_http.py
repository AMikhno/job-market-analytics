"""HTTP session: retry policy and the split connect/read timeout."""

import pytest
import requests
import responses

from shared.http import build_session, get_json


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


def test_get_json_uses_split_connect_read_timeout() -> None:
    """A stalled connect must fail fast (10s) and be retried, not block on the
    full read budget — so the timeout is passed as a (connect, read) tuple."""
    captured: dict[str, object] = {}

    class _Resp:
        def raise_for_status(self) -> None: ...
        def json(self) -> dict[str, int]:
            return {"ok": 1}

    class _Session:
        def get(self, url: str, timeout: object = None) -> _Resp:
            captured["timeout"] = timeout
            return _Resp()

    assert get_json(_Session(), "https://api.example.test/x", min_interval_s=0) == {"ok": 1}
    assert captured["timeout"] == (10, 30)
