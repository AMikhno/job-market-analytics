"""Email digest: new gold postings since the last digest, via SMTP.

Delivery model (ADR-0019): after each successful prod run, email only postings
whose first_seen_at is newer than the last digest's watermark (ops.digest_runs),
ordered by the soft signals. When nothing is new, a short "no new jobs" heartbeat
is still sent (so a healthy-but-quiet run is visible) but the watermark/ledger is
left untouched — digest_runs tracks delivered postings, not empty pings. Ingest
warnings ride along as a footer. Failure alerting stays GitHub-native (failed-run
email) — this module delivers content, it is not the alarm channel.

Posting fields are untrusted input from the web; the HTML part escapes every
field and never embeds description_html.
"""

from __future__ import annotations

import html
import logging
import smtplib
import sys
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from shared import storage
from shared.config import Settings, get_settings
from shared.models import RunSummary

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("deliver")


def fetch_new_postings(settings: Settings, watermark: str) -> list[dict[str, object]]:
    """Gold postings first seen after the watermark, best fit first.

    Ordered by the single `match_score` (ADR-0024) rather than by several keys in
    priority order. The old form sorted on title_match before desired_tech_hits,
    so the tech count printed on each line reset partway down the email — the
    visible number was not the sort key. Now it is.
    """
    sql = f"""
        select title, company, location, url, match_score, desired_tech_hits,
               title_match, deal_breaker_hits, deal_breaker_terms, first_seen_at
        from {storage.gold_table(settings)}
        where first_seen_at > cast(? as timestamp)
        order by match_score desc, posted_or_updated_at desc nulls last
    """
    return storage.query_rows(sql, params=[watermark], settings=settings)


def read_run_summary(settings: Settings) -> RunSummary | None:
    """This run's ingest summary, or None when there isn't one.

    None is a real state, not an empty summary: the digest can run standalone,
    after a run whose summary wasn't kept. Keeping it distinct is what lets the
    footer say "unknown" instead of implying health it never verified.
    """
    path = Path(settings.summary_path)
    if not path.exists():
        return None
    return RunSummary.model_validate_json(path.read_text())


def _footer(summary: RunSummary | None) -> str:
    """One line stating ingest health, in every digest.

    Always present, so a silent footer can no longer mean either "healthy" or
    "we never checked" -- the two now read differently. Two clauses are appended
    to whatever the headline is, because both describe ways the stream thins out
    while every source that ran stayed "ok":
      * skipped boards -- a board that 404s (the company moved ATS, the board
        came down) leaves its source "ok";
      * unconfigured sources -- a source with no boards in the company list
        never runs at all, so it is not even counted in the headline.
    """
    if summary is None:
        return "Ingest status unknown for this digest (no run summary was found)."
    return " ".join(p for p in (_health(summary), _unconfigured(summary), _skipped(summary)) if p)


def _health(summary: RunSummary) -> str:
    if summary.failures:
        return f"Ingest FAILED for: {', '.join(summary.failures)}."
    if summary.warnings:
        return f"Warnings: low/zero volume from {', '.join(summary.warnings)}."
    n = len(summary.sources)
    if not n:
        return "No sources ran in this ingest."
    return f"All {n} source{'s' if n != 1 else ''} healthy ({summary.board_count} boards checked)."


def _unconfigured(summary: RunSummary) -> str:
    """Sources that are registered and active but got no boards from the list.

    Appended to the health line rather than replacing it, because the two
    statements are both true and only useless apart: "all 3 sources healthy
    (122 boards checked)" was accurate every run for the four days the six
    Tier-1 sources were missing from the CI company list. Source names are ATS
    names, so unlike skipped board refs they need no redaction.
    """
    names = summary.unconfigured
    if not names:
        return ""
    return (
        f"{len(names)} registered source(s) had no active boards: {', '.join(names)} "
        "— check the company list (CI reads the COMPANIES_CSV_CONTENT variable)."
    )


def _skipped(summary: RunSummary) -> str:
    """Redacted refs only -- the digest is private, but this string also rides
    along into the run summary and step output. `make whois` maps them back."""
    refs = summary.skipped_refs
    if not refs:
        return ""
    return (
        f"{len(refs)} board(s) skipped this run: {', '.join(refs)} "
        "— identify with `make whois REF=<ref>`."
    )


def build_email(
    rows: list[dict[str, object]], summary: RunSummary | None, settings: Settings
) -> EmailMessage:
    """Multipart text+HTML message. Every posting field is escaped in the HTML
    part — posting metadata is scraped web content, not trusted markup."""
    msg = EmailMessage()
    if rows:
        msg["Subject"] = f"{len(rows)} new job posting{'s' if len(rows) != 1 else ''}"
    else:
        msg["Subject"] = "No new jobs since the last run"
    msg["From"] = settings.smtp_user
    msg["To"] = settings.digest_to or settings.smtp_user

    text_lines: list[str] = []
    html_items: list[str] = []
    for r in rows:
        title, company = str(r["title"]), str(r["company"])
        location = str(r["location"]) if r["location"] is not None else "location unknown"
        url = str(r["url"])
        # The score leads, then the parts that produced it — the list is ordered
        # by that first number, so the ranking is checkable from the line itself.
        signals = f"match {r['match_score']} — tech hits: {r['desired_tech_hits']}"
        if r["title_match"]:
            signals += ", title match"
        # Named, not just counted: "Kafka" as a nice-to-have reads very
        # differently from a posting built on Spark + Flink, and only you can
        # tell which from the line.
        if r.get("deal_breaker_terms"):
            signals += f", mentions {r['deal_breaker_terms']}"
        text_lines.append(f"- {title} @ {company} ({location}) [{signals}]\n  {url}")
        html_items.append(
            f'<li><a href="{html.escape(url, quote=True)}">{html.escape(title)}</a>'
            f" @ {html.escape(company)} ({html.escape(location)})"
            f" <small>[{html.escape(signals)}]</small></li>"
        )

    status = _footer(summary)
    footer_text = f"\n\n{status}"
    footer_html = f"<p><small>{html.escape(status)}</small></p>"

    if rows:
        body_text = "\n".join(text_lines)
        body_html = f"<ul>{''.join(html_items)}</ul>"
    else:
        body_text = "No new job postings since the last digest."
        body_html = f"<p>{body_text}</p>"

    msg.set_content(body_text + footer_text)
    msg.add_alternative(body_html + footer_html, subtype="html")
    return msg


def _send(msg: EmailMessage, settings: Settings) -> None:
    with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port) as smtp:
        smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)


def _iso(value: object) -> str:
    """Timestamp cell (datetime from either warehouse, or string) -> ISO text."""
    return value.isoformat() if isinstance(value, datetime) else str(value)


def run() -> int:
    settings = get_settings()
    if not settings.smtp_user or not settings.smtp_password:
        # Deliberate no-op, not a swallowed error: the digest is an optional
        # feature that dev/CI runs without; prod injects the secrets.
        log.warning("digest disabled: SMTP_USER / SMTP_PASSWORD not configured")
        return 0

    storage.ensure_digest_table(settings)
    watermark = storage.latest_digest_watermark(settings)
    if watermark is None:
        bootstrap = datetime.now(UTC) - timedelta(hours=settings.digest_lookback_hours)
        watermark = bootstrap.isoformat()
        log.info("first digest: bootstrapping watermark to %s", watermark)

    rows = fetch_new_postings(settings, watermark)
    msg = build_email(rows, read_run_summary(settings), settings)
    _send(msg, settings)

    if not rows:
        # Heartbeat only: nothing new, so the content watermark/ledger is left
        # untouched (digest_runs records delivered postings, not empty pings).
        log.info("digest: no new postings after %s; sent heartbeat", watermark)
        return 0

    new_watermark = max(_iso(r["first_seen_at"]) for r in rows)
    storage.land_digest(
        sent_at=datetime.now(UTC).isoformat(),
        watermark=new_watermark,
        postings_sent=len(rows),
        settings=settings,
    )
    log.info(
        "digest sent: %d posting(s) to %s; watermark -> %s",
        len(rows),
        msg["To"],
        new_watermark,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
