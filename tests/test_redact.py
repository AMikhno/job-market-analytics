"""Company identifiers must not reach a log line verbatim by default."""

from __future__ import annotations

import logging

import pytest

from ingest.pipeline import _label
from shared.config import Settings
from shared.redact import redact_ref


def test_redact_ref_is_stable() -> None:
    assert redact_ref("acme-corp") == redact_ref("acme-corp")


def test_redact_ref_hides_the_value() -> None:
    out = redact_ref("acme-corp")
    assert "acme" not in out
    assert out.startswith("redacted:")


def test_redact_ref_distinguishes_boards() -> None:
    assert redact_ref("acme-corp") != redact_ref("globex")


def test_label_redacts_by_default() -> None:
    assert _label("acme-corp", Settings()) == redact_ref("acme-corp")


def test_label_passes_through_when_disabled() -> None:
    assert _label("acme-corp", Settings(redact_company_logs=False)) == "acme-corp"


@pytest.mark.parametrize("redact", [True, False])
def test_hard_failure_log_never_carries_board_refs(
    caplog: pytest.LogCaptureFixture, redact: bool
) -> None:
    """_finalize logs sources only -- r.error embeds raw refs, so it must not be logged."""
    from datetime import UTC, datetime

    from ingest.pipeline import _finalize
    from shared.models import IngestRun

    now = datetime.now(UTC)
    run = IngestRun(
        run_id="r1",
        source="greenhouse",
        company_count=1,
        rows_fetched=0,
        status="error",
        started_at=now,
        finished_at=now,
        error="all boards failed: ['secret-board-ref']",
    )
    settings = Settings(redact_company_logs=redact, summary_path="/dev/null")

    with caplog.at_level(logging.ERROR):
        assert _finalize([run], settings) == 1

    assert "secret-board-ref" not in caplog.text
    assert "greenhouse" in caplog.text
