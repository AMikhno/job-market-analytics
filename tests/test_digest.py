"""Digest behavior against a real (temp) DuckDB gold table; SMTP is stubbed."""

import json
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import duckdb
import pytest

from deliver import digest
from shared import storage
from shared.config import Settings

NOW = datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)


def _settings(tmp_path, **overrides) -> Settings:
    defaults = dict(
        _env_file=None,
        duckdb_path=str(tmp_path / "jobs.duckdb"),
        summary_path=str(tmp_path / "ingest_summary.json"),
        smtp_user="me@example.com",
        smtp_password="app-password",
    )
    return Settings(**{**defaults, **overrides})


def _seed_gold(settings: Settings, rows: list[dict]) -> None:
    con = duckdb.connect(settings.duckdb_path)
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS main_gold")
        con.execute(
            """CREATE TABLE IF NOT EXISTS main_gold.fct_job_postings (
                title VARCHAR, company VARCHAR, location VARCHAR, url VARCHAR,
                desired_tech_hits BIGINT, title_match BOOLEAN,
                first_seen_at TIMESTAMP, posted_or_updated_at TIMESTAMP)"""
        )
        for r in rows:
            # Insert naive-UTC: binding an aware datetime makes DuckDB localize
            # it, while the pipeline's own ISO strings keep their UTC wall-clock.
            first_seen = r["first_seen_at"].replace(tzinfo=None)
            con.execute(
                "INSERT INTO main_gold.fct_job_postings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    r.get("title", "Analytics Engineer"),
                    r.get("company", "acme"),
                    r.get("location", "Ottawa"),
                    r.get("url", "https://example/x"),
                    r.get("desired_tech_hits", 0),
                    r.get("title_match", False),
                    first_seen,
                    first_seen,
                ],
            )
    finally:
        con.close()


class _StubSMTP:
    """Captures send_message instead of talking to a server."""

    sent: list[EmailMessage] = []
    logins: list[tuple[str, str]] = []

    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port

    def __enter__(self) -> _StubSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def login(self, user: str, password: str) -> None:
        _StubSMTP.logins.append((user, password))

    def send_message(self, msg: EmailMessage) -> None:
        _StubSMTP.sent.append(msg)


@pytest.fixture
def stub_smtp(monkeypatch) -> type[_StubSMTP]:
    _StubSMTP.sent, _StubSMTP.logins = [], []
    monkeypatch.setattr(digest.smtplib, "SMTP_SSL", _StubSMTP)
    return _StubSMTP


def test_disabled_without_credentials(tmp_path, monkeypatch, stub_smtp) -> None:
    settings = _settings(tmp_path, smtp_user="", smtp_password="")
    monkeypatch.setattr(digest, "get_settings", lambda: settings)
    assert digest.run() == 0
    assert stub_smtp.sent == []


def test_sends_new_postings_and_advances_watermark(tmp_path, monkeypatch, stub_smtp) -> None:
    settings = _settings(tmp_path)
    fresh = NOW - timedelta(hours=1)
    stale = NOW - timedelta(hours=40)  # outside the 26h bootstrap lookback
    _seed_gold(
        settings,
        [
            {
                "title": "Analytics Engineer",
                "first_seen_at": fresh,
                "title_match": True,
                "desired_tech_hits": 3,
                "url": "https://example/new",
            },
            {"title": "Old Posting", "first_seen_at": stale},
        ],
    )
    monkeypatch.setattr(digest, "get_settings", lambda: settings)

    assert digest.run() == 0

    (msg,) = stub_smtp.sent
    assert msg["Subject"] == "1 new job posting"
    assert msg["To"] == "me@example.com"  # digest_to defaults to smtp_user
    body = msg.get_body(("plain",)).get_content()
    assert "Analytics Engineer" in body and "Old Posting" not in body
    assert stub_smtp.logins == [("me@example.com", "app-password")]
    # watermark row recorded so the next run starts from `fresh` (stored naive-UTC)
    assert storage.latest_digest_watermark(settings) == fresh.replace(tzinfo=None).isoformat()


def test_no_email_when_nothing_new(tmp_path, monkeypatch, stub_smtp) -> None:
    settings = _settings(tmp_path)
    seen = NOW - timedelta(hours=2)
    _seed_gold(settings, [{"first_seen_at": seen}])
    monkeypatch.setattr(digest, "get_settings", lambda: settings)

    assert digest.run() == 0
    assert len(stub_smtp.sent) == 1
    assert digest.run() == 0  # second run: watermark == first_seen_at -> nothing new
    assert len(stub_smtp.sent) == 1


def test_email_escapes_posting_fields_and_includes_warnings(tmp_path) -> None:
    settings = _settings(tmp_path)
    rows = [
        {
            "title": 'Engineer <script>alert("x")</script>',
            "company": "a&b",
            "location": None,
            "url": "https://example/x?a=1&b=2",
            "desired_tech_hits": 2,
            "title_match": True,
            "first_seen_at": NOW,
        }
    ]
    msg = digest.build_email(rows, ["lever"], settings)

    html_part = msg.get_body(("html",)).get_content()
    assert "<script>" not in html_part  # untrusted fields are escaped
    assert "&lt;script&gt;" in html_part
    assert "a&amp;b" in html_part
    assert "location unknown" in html_part
    assert "low/zero volume from lever" in html_part
    text_part = msg.get_body(("plain",)).get_content()
    assert "title match" in text_part
    assert "Warnings: low/zero volume from lever." in text_part


def test_read_warnings_from_summary(tmp_path) -> None:
    settings = _settings(tmp_path)
    assert digest.read_warnings(settings) == []  # no summary file: standalone run
    (tmp_path / "ingest_summary.json").write_text(json.dumps({"warnings": ["ashby"]}))
    assert digest.read_warnings(settings) == ["ashby"]


def test_ordering_puts_best_signals_first(tmp_path, monkeypatch, stub_smtp) -> None:
    settings = _settings(tmp_path)
    fresh = NOW - timedelta(hours=1)
    _seed_gold(
        settings,
        [
            {"title": "Plain", "first_seen_at": fresh},
            {"title": "Best", "first_seen_at": fresh, "title_match": True, "desired_tech_hits": 5},
            {"title": "Middling", "first_seen_at": fresh, "desired_tech_hits": 2},
        ],
    )
    monkeypatch.setattr(digest, "get_settings", lambda: settings)

    digest.run()

    body = stub_smtp.sent[0].get_body(("plain",)).get_content()
    assert body.index("Best") < body.index("Middling") < body.index("Plain")
