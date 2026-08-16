"""Digest behavior against a real (temp) DuckDB gold table; SMTP is stubbed."""

import json
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

import duckdb
import pytest

from deliver import digest
from shared import storage
from shared.config import Settings
from shared.models import RunSummary, SourceSummary, VolumeDrop

# Anchored to the real clock (not a fixed date): run() bootstraps the first
# watermark from datetime.now(UTC) - digest_lookback_hours, so seeded rows must
# be offset from *now* or they drift out of the lookback window as time passes.
NOW = datetime.now(UTC).replace(microsecond=0)


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
                deal_breaker_hits BIGINT, deal_breaker_terms VARCHAR,
                match_score BIGINT, fit_score BIGINT,
                company_type VARCHAR, geo_restriction VARCHAR, manages_people VARCHAR,
                similarity DOUBLE, best_match_source VARCHAR,
                first_seen_at TIMESTAMP, posted_or_updated_at TIMESTAMP)"""
        )
        for r in rows:
            # Insert naive-UTC: binding an aware datetime makes DuckDB localize
            # it, while the pipeline's own ISO strings keep their UTC wall-clock.
            first_seen = r["first_seen_at"].replace(tzinfo=None)
            con.execute(
                "INSERT INTO main_gold.fct_job_postings "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    r.get("title", "Analytics Engineer"),
                    r.get("company", "acme"),
                    r.get("location", "Ottawa"),
                    r.get("url", "https://example/x"),
                    r.get("desired_tech_hits", 0),
                    r.get("title_match", False),
                    r.get("deal_breaker_hits", 0),
                    r.get("deal_breaker_terms"),
                    # mirrors silver's formula (bonus 2 / penalty 1) unless pinned
                    r.get(
                        "match_score",
                        r.get("desired_tech_hits", 0)
                        + (2 if r.get("title_match", False) else 0)
                        - r.get("deal_breaker_hits", 0),
                    ),
                    # Default null: unscored is the normal state until V2 has run,
                    # and the state the digest must handle without special-casing.
                    r.get("fit_score"),
                    r.get("company_type"),
                    r.get("geo_restriction"),
                    r.get("manages_people"),
                    r.get("similarity"),
                    r.get("best_match_source"),
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


def test_sends_heartbeat_when_nothing_new(tmp_path, monkeypatch, stub_smtp) -> None:
    settings = _settings(tmp_path)
    seen = NOW - timedelta(hours=2)
    _seed_gold(settings, [{"first_seen_at": seen}])
    monkeypatch.setattr(digest, "get_settings", lambda: settings)

    # First run delivers the one posting and advances the watermark to `seen`.
    assert digest.run() == 0
    assert len(stub_smtp.sent) == 1
    watermark_after_send = storage.latest_digest_watermark(settings)

    # Second run: nothing new -> a "no new jobs" heartbeat is still sent, but the
    # watermark/ledger is left untouched (heartbeats are not delivered content).
    assert digest.run() == 0
    assert len(stub_smtp.sent) == 2
    heartbeat = stub_smtp.sent[1]
    assert heartbeat["Subject"] == "No new jobs since the last run"
    assert "No new job postings" in heartbeat.get_body(("plain",)).get_content()
    assert storage.latest_digest_watermark(settings) == watermark_after_send


def test_heartbeat_carries_warning_footer(tmp_path, monkeypatch, stub_smtp) -> None:
    settings = _settings(tmp_path)
    (tmp_path / "ingest_summary.json").write_text(json.dumps({"warnings": ["lever"]}))
    _seed_gold(settings, [{"first_seen_at": NOW - timedelta(hours=200)}])  # outside lookback
    monkeypatch.setattr(digest, "get_settings", lambda: settings)

    assert digest.run() == 0
    (heartbeat,) = stub_smtp.sent
    assert heartbeat["Subject"] == "No new jobs since the last run"
    assert "Warnings: low/zero volume from lever." in heartbeat.get_body(("plain",)).get_content()
    assert storage.latest_digest_watermark(settings) is None  # nothing landed


def test_email_escapes_posting_fields_and_includes_warnings(tmp_path) -> None:
    settings = _settings(tmp_path)
    rows = [
        {
            "title": 'Engineer <script>alert("x")</script>',
            "company": "a&b",
            "location": None,
            "url": "https://example/x?a=1&b=2",
            "match_score": 4,
            "desired_tech_hits": 2,
            "title_match": True,
            "first_seen_at": NOW,
        }
    ]
    msg = digest.build_email(rows, RunSummary(warnings=["lever"]), settings)

    html_part = msg.get_body(("html",)).get_content()
    assert "<script>" not in html_part  # untrusted fields are escaped
    assert "&lt;script&gt;" in html_part
    assert "a&amp;b" in html_part
    assert "location unknown" in html_part
    assert "low/zero volume from lever" in html_part
    text_part = msg.get_body(("plain",)).get_content()
    assert "title match" in text_part
    assert "Warnings: low/zero volume from lever." in text_part


def test_read_run_summary_absent_is_none(tmp_path) -> None:
    """Absent file is None, not an empty summary -- the footer says so."""
    assert digest.read_run_summary(_settings(tmp_path)) is None


def test_read_run_summary_parses_file(tmp_path) -> None:
    settings = _settings(tmp_path)
    (tmp_path / "ingest_summary.json").write_text(json.dumps({"warnings": ["ashby"]}))
    summary = digest.read_run_summary(settings)
    assert summary is not None
    assert summary.warnings == ["ashby"]


def test_footer_states_are_distinguishable() -> None:
    """The bug this closes: 'healthy' and 'never checked' used to look identical."""
    healthy = RunSummary(
        sources=[
            SourceSummary(source="greenhouse", rows=10, status="ok", company_count=100),
            SourceSummary(source="lever", rows=5, status="ok", company_count=41),
        ]
    )
    assert digest._footer(healthy) == "All 2 sources healthy (141 boards checked)."
    assert "unknown" in digest._footer(None)
    assert digest._footer(RunSummary(warnings=["lever"])).startswith("Warnings:")
    assert digest._footer(RunSummary(failures=["ashby"])).startswith("Ingest FAILED")
    assert digest._footer(RunSummary()) == "No sources ran in this ingest."


def test_footer_singular_source(tmp_path) -> None:
    one = RunSummary(sources=[SourceSummary(source="lever", rows=1, status="ok", company_count=3)])
    assert digest._footer(one) == "All 1 source healthy (3 boards checked)."


def test_footer_reports_skipped_boards_even_when_healthy() -> None:
    """A skipped board leaves its source "ok", so it used to be invisible: the
    footer said "all sources healthy" while a company dropped out of the list."""
    summary = RunSummary(
        sources=[
            SourceSummary(
                source="ashby",
                rows=40,
                status="ok",
                company_count=7,
                skipped_refs=["redacted:ad589ceb"],
            )
        ]
    )

    footer = digest._footer(summary)

    assert "All 1 source healthy" in footer  # headline still reported
    assert "1 board(s) skipped" in footer
    assert "redacted:ad589ceb" in footer
    assert "make whois" in footer


def test_fit_score_leads_the_ordering(tmp_path: Path) -> None:
    """The LLM score orders the digest; match_score breaks ties beneath it.

    The weak-keyword/strong-fit posting is the point of V2 — under V1's ordering
    it sorted last, and it is the case relevance-signals found the keyword rules
    getting wrong.
    """
    settings = _settings(tmp_path)
    now = datetime.now(UTC)
    _seed_gold(
        settings,
        [
            {
                "title": "Strong keywords weak fit",
                "fit_score": 2,
                "match_score": 9,
                "first_seen_at": now,
            },
            {
                "title": "Weak keywords strong fit",
                "fit_score": 5,
                "match_score": 0,
                "first_seen_at": now,
            },
        ],
    )

    rows = digest.fetch_new_postings(settings, (now - timedelta(hours=1)).isoformat())

    assert [r["title"] for r in rows] == [
        "Weak keywords strong fit",
        "Strong keywords weak fit",
    ]


def test_unscored_postings_sort_last_but_still_ship(tmp_path: Path) -> None:
    """`nulls last` is what keeps the score an ordering and not a filter
    (ADR-0020): an unscored posting sinks below scored ones, and is delivered."""
    settings = _settings(tmp_path)
    now = datetime.now(UTC)
    _seed_gold(
        settings,
        [
            {"title": "Unscored", "match_score": 99, "first_seen_at": now},
            {"title": "Scored low", "fit_score": 1, "match_score": 0, "first_seen_at": now},
        ],
    )

    rows = digest.fetch_new_postings(settings, (now - timedelta(hours=1)).isoformat())

    assert [r["title"] for r in rows] == ["Scored low", "Unscored"]


def test_digest_line_states_unscored_rather_than_omitting_it(tmp_path: Path) -> None:
    """A missing fit reads as a low one if the line just leaves it out, and the
    two mean opposite things about a posting."""
    settings = _settings(tmp_path)
    now = datetime.now(UTC)
    _seed_gold(
        settings,
        [
            {"title": "Has a score", "fit_score": 4, "first_seen_at": now},
            {"title": "Has none", "first_seen_at": now},
        ],
    )

    rows = digest.fetch_new_postings(settings, (now - timedelta(hours=1)).isoformat())
    body = digest.build_email(rows, None, settings).get_body(("plain",)).get_content()

    assert "LLM 4/5" in body
    assert "unscored" in body


def test_a_canada_ok_posting_gets_no_location_label(tmp_path: Path) -> None:
    """The exception is annotated, not the norm. Printing "canada ok" on every
    good line makes the label furniture — the eye stops reading it, which is
    exactly when the one that matters slips past."""
    settings = _settings(tmp_path)
    now = datetime.now(UTC)
    _seed_gold(
        settings,
        [
            {
                "title": "Fine",
                "fit_score": 4,
                "company_type": "B2B SaaS",
                "geo_restriction": "canada_ok",
                "manages_people": "no",
                "first_seen_at": now,
            }
        ],
    )

    rows = digest.fetch_new_postings(settings, (now - timedelta(hours=1)).isoformat())
    body = digest.build_email(rows, None, settings).get_body(("plain",)).get_content()

    assert "canada" not in body.lower()
    assert "B2B SaaS" in body
    # The good case prints nothing: showing "no reports" on every line makes it
    # furniture, the same reason canada_ok is silent.
    assert "reports" not in body
    assert "manages people" not in body


def test_the_two_ai_scores_are_labelled_by_method(tmp_path: Path) -> None:
    """They disagree, and until human labels say which is right, seeing them
    disagree is the point — so they are never merged into one number. Similarity
    prints as a percentage so it cannot read as a second 1-5 rating."""
    settings = _settings(tmp_path)
    now = datetime.now(UTC)
    _seed_gold(
        settings,
        [
            {
                "title": "Both",
                "match_score": 11,
                "fit_score": 4,
                "similarity": 0.8231,
                "desired_tech_hits": 9,
                "title_match": True,
                "deal_breaker_terms": "Spark",
                "company_type": "B2B SaaS",
                "geo_restriction": "us_only",
                "manages_people": "yes",
                "first_seen_at": now,
            }
        ],
    )

    rows = digest.fetch_new_postings(settings, (now - timedelta(hours=1)).isoformat())
    body = digest.build_email(rows, None, settings).get_body(("plain",)).get_content()

    assert "match 11 — LLM 4/5, vectors 82% — tech hits: 9" in body
    assert "title match" in body
    assert "mentions Spark" in body
    assert "B2B SaaS" in body
    assert "text says US only" in body
    assert "manages people" in body


def test_a_posting_with_neither_ai_score_says_unscored(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    now = datetime.now(UTC)
    _seed_gold(settings, [{"title": "Plain", "match_score": 3, "first_seen_at": now}])

    rows = digest.fetch_new_postings(settings, (now - timedelta(hours=1)).isoformat())
    body = digest.build_email(rows, None, settings).get_body(("plain",)).get_content()

    assert "match 3 — unscored — tech hits: 0" in body


def test_a_us_only_posting_is_called_out(tmp_path: Path) -> None:
    """The case the location rule provably cannot catch: silver keeps bare
    "Remote", so this arrives with a relevant title and the restriction stated
    only in the description."""
    settings = _settings(tmp_path)
    now = datetime.now(UTC)
    _seed_gold(
        settings,
        [
            {"title": "Restricted", "geo_restriction": "us_only", "first_seen_at": now},
            {"title": "Vague", "geo_restriction": "unclear", "first_seen_at": now},
        ],
    )

    rows = digest.fetch_new_postings(settings, (now - timedelta(hours=1)).isoformat())
    body = digest.build_email(rows, None, settings).get_body(("plain",)).get_content()

    assert "text says US only" in body
    assert "eligibility unstated" in body


def test_a_us_only_posting_is_still_delivered(tmp_path: Path) -> None:
    """Annotate, never drop (ADR-0015 / ADR-0020). Postings lie about location
    in both directions, so hiding on an extracted value would lose real roles to
    a model's mistake — it is labelled and left in."""
    settings = _settings(tmp_path)
    now = datetime.now(UTC)
    _seed_gold(
        settings,
        [{"title": "Restricted", "geo_restriction": "us_only", "first_seen_at": now}],
    )

    rows = digest.fetch_new_postings(settings, (now - timedelta(hours=1)).isoformat())

    assert [r["title"] for r in rows] == ["Restricted"]


def test_unextracted_annotations_are_simply_absent(tmp_path: Path) -> None:
    """Nothing extracted yet is the normal state before V2 has run, and it must
    not produce empty separators or the word None in an email."""
    settings = _settings(tmp_path)
    now = datetime.now(UTC)
    _seed_gold(settings, [{"title": "Plain", "first_seen_at": now}])

    rows = digest.fetch_new_postings(settings, (now - timedelta(hours=1)).isoformat())
    body = digest.build_email(rows, None, settings).get_body(("plain",)).get_content()

    assert "None" not in body
    assert "—  —" not in body


def test_ordering_is_unchanged_while_nothing_is_scored(tmp_path: Path) -> None:
    """V2 must not disturb delivery before it produces anything: with every
    fit_score null the order is exactly V1's match_score order."""
    settings = _settings(tmp_path)
    now = datetime.now(UTC)
    _seed_gold(
        settings,
        [
            {"title": "Low", "match_score": 1, "first_seen_at": now},
            {"title": "High", "match_score": 8, "first_seen_at": now},
            {"title": "Mid", "match_score": 4, "first_seen_at": now},
        ],
    )

    rows = digest.fetch_new_postings(settings, (now - timedelta(hours=1)).isoformat())

    assert [r["title"] for r in rows] == ["High", "Mid", "Low"]


def test_footer_skipped_refs_are_never_raw() -> None:
    """The footer text also reaches the run summary and the public step output,
    so it must carry digests only -- never a board_ref."""
    summary = RunSummary(
        sources=[SourceSummary(source="ashby", rows=1, status="ok", skipped_refs=["redacted:ff01"])]
    )
    assert "dominion" not in digest._footer(summary).lower()


def test_footer_omits_the_skipped_clause_when_there_are_none() -> None:
    clean = RunSummary(
        sources=[SourceSummary(source="lever", rows=9, status="ok", company_count=2)]
    )
    assert "skipped" not in digest._footer(clean)


def test_footer_reports_a_volume_drop_even_when_healthy() -> None:
    """A source that lost most of its boards still reports status "ok" and still
    lands plenty of rows, so the health line calls it healthy. Only the
    comparison against its own recent runs shows it."""
    summary = RunSummary(
        sources=[SourceSummary(source="bamboohr", rows=61, status="ok", company_count=32)],
        volume_drops=[VolumeDrop(source="bamboohr", rows=61, baseline=153)],
    )

    footer = digest._footer(summary)

    assert "All 1 source healthy" in footer  # headline still reported
    assert "Volume dropped sharply for 1 source(s)" in footer
    assert "bamboohr 61 vs ~153" in footer


def test_footer_omits_the_volume_clause_when_steady() -> None:
    clean = RunSummary(
        sources=[SourceSummary(source="lever", rows=9, status="ok", company_count=2)]
    )
    assert "dropped sharply" not in digest._footer(clean)


def test_footer_reports_sources_that_never_ran_even_when_healthy() -> None:
    """A source with no boards in the company list produces no SourceSummary, so
    the health line counts only what ran and reads as fully healthy. This is the
    shape of the four-day outage: six Tier-1 sources were missing from the CI
    company list and every digest said "all sources healthy" while their raw
    tables aged past the freshness gate."""
    summary = RunSummary(
        sources=[SourceSummary(source="lever", rows=400, status="ok", company_count=122)],
        unconfigured=["bamboohr", "pinpoint", "recruitee"],
    )

    footer = digest._footer(summary)

    assert "All 1 source healthy (122 boards checked)." in footer  # headline still reported
    assert "3 registered source(s) had no active boards" in footer
    assert "bamboohr, pinpoint, recruitee" in footer
    assert "COMPANIES_CSV_CONTENT" in footer  # points at the list, not the warehouse


def test_footer_omits_the_unconfigured_clause_when_every_source_ran() -> None:
    clean = RunSummary(
        sources=[SourceSummary(source="lever", rows=9, status="ok", company_count=2)]
    )
    assert "no active boards" not in digest._footer(clean)


def test_healthy_digest_states_health_explicitly(tmp_path, monkeypatch, stub_smtp) -> None:
    """A clean run must say so, not fall silent."""
    settings = _settings(tmp_path)
    (tmp_path / "ingest_summary.json").write_text(
        RunSummary(
            sources=[SourceSummary(source="lever", rows=7, status="ok", company_count=12)]
        ).model_dump_json()
    )
    _seed_gold(settings, [{"first_seen_at": NOW - timedelta(hours=200)}])
    monkeypatch.setattr(digest, "get_settings", lambda: settings)

    assert digest.run() == 0
    (sent,) = stub_smtp.sent
    assert "All 1 source healthy (12 boards checked)." in sent.get_body(("plain",)).get_content()


def test_missing_summary_says_unknown(tmp_path, monkeypatch, stub_smtp) -> None:
    settings = _settings(tmp_path)  # no ingest_summary.json written
    _seed_gold(settings, [{"first_seen_at": NOW - timedelta(hours=200)}])
    monkeypatch.setattr(digest, "get_settings", lambda: settings)

    assert digest.run() == 0
    (sent,) = stub_smtp.sent
    assert "Ingest status unknown" in sent.get_body(("plain",)).get_content()


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


def test_deal_breaker_demotes_but_does_not_delete_or_outrank_fit(
    tmp_path, monkeypatch, stub_smtp
) -> None:
    """ADR-0023 + ADR-0024: a deal-breaker costs a posting points, it does not
    remove it and it does not send it to the bottom regardless of fit. A strong
    match carrying one incidental "Kafka" still outranks a weak clean posting —
    but loses to its own twin without the mention."""
    settings = _settings(tmp_path)
    fresh = NOW - timedelta(hours=1)
    _seed_gold(
        settings,
        [
            {
                "title": "Strong but mentions Kafka",
                "first_seen_at": fresh,
                "title_match": True,
                "desired_tech_hits": 6,
                "deal_breaker_hits": 1,
                "deal_breaker_terms": "Kafka",
            },  # 6 + 2 - 1 = 7
            {
                "title": "Strong and clean",
                "first_seen_at": fresh,
                "title_match": True,
                "desired_tech_hits": 6,
            },  # 6 + 2 = 8
            {"title": "Clean and plain", "first_seen_at": fresh},  # 0
        ],
    )
    monkeypatch.setattr(digest, "get_settings", lambda: settings)

    digest.run()

    body = stub_smtp.sent[0].get_body(("plain",)).get_content()
    assert "Strong but mentions Kafka" in body  # delivered, not filtered out
    assert (
        body.index("Strong and clean")
        < body.index("Strong but mentions Kafka")
        < body.index("Clean and plain")
    )
    assert "mentions Kafka" in body  # named, so it can be judged from the line


def test_digest_line_shows_the_score_it_is_sorted_by(tmp_path, monkeypatch, stub_smtp) -> None:
    """The printed number has to be the sort key. Before ADR-0024 the line showed
    tech hits while the sort led on title_match, so the visible number reset
    partway down the email and the ordering looked arbitrary."""
    settings = _settings(tmp_path)
    fresh = NOW - timedelta(hours=1)
    _seed_gold(
        settings,
        [
            {"title": "Tech only", "first_seen_at": fresh, "desired_tech_hits": 8},  # 8
            {
                "title": "Title only",
                "first_seen_at": fresh,
                "title_match": True,
                "desired_tech_hits": 0,
            },  # 2
        ],
    )
    monkeypatch.setattr(digest, "get_settings", lambda: settings)

    digest.run()

    body = stub_smtp.sent[0].get_body(("plain",)).get_content()
    # a title match no longer jumps the queue ahead of a much stronger tech match
    assert body.index("Tech only") < body.index("Title only")
    assert "match 8 — unscored — tech hits: 8" in body
    assert "match 2 — unscored — tech hits: 0, title match" in body
    # the scores read down the page in descending order
    scores = [
        int(line.split("match ")[1].split(" ")[0]) for line in body.splitlines() if "match " in line
    ]
    assert scores == sorted(scores, reverse=True)
