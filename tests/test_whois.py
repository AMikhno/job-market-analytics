"""Reversing a redacted CI-log ref against the local company list."""

from __future__ import annotations

from pathlib import Path

import pytest

from ingest import whois
from shared.config import Settings
from shared.redact import redact_ref

_CSV = (
    "company_name,source,board_ref,active,tier,notes\n"
    "Acme Corp,greenhouse,acmecorp,true,1,\n"
    "Globex,lever,globex,false,2,\n"
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    path = tmp_path / "companies.csv"
    path.write_text(_CSV)
    return Settings(companies_csv=str(path))


def test_resolves_board_ref(settings: Settings) -> None:
    (match,) = whois.resolve(redact_ref("acmecorp"), settings)
    assert match.company_name == "Acme Corp"


def test_resolves_company_name(settings: Settings) -> None:
    (match,) = whois.resolve(redact_ref("Acme Corp"), settings)
    assert match.board_ref == "acmecorp"


def test_accepts_bare_digest_without_prefix(settings: Settings) -> None:
    bare = redact_ref("acmecorp").removeprefix("redacted:")
    (match,) = whois.resolve(bare, settings)
    assert match.board_ref == "acmecorp"


def test_finds_inactive_rows_too(settings: Settings) -> None:
    """An inactive board still explains a log line from before it was disabled."""
    (match,) = whois.resolve(redact_ref("globex"), settings)
    assert match.active is False


def test_unknown_ref_returns_empty(settings: Settings) -> None:
    assert whois.resolve("redacted:deadbeef", settings) == []


def test_main_reports_no_match(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(whois, "get_settings", lambda: settings)
    assert whois.main(["redacted:deadbeef"]) == 1


def test_main_succeeds_on_match(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(whois, "get_settings", lambda: settings)
    assert whois.main([redact_ref("acmecorp")]) == 0


def test_main_without_args_is_a_usage_error() -> None:
    assert whois.main([]) == 2
