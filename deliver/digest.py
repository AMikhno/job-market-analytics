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
import json
import logging
import smtplib
import sys
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from shared import storage
from shared.config import Settings, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("deliver")


def fetch_new_postings(settings: Settings, watermark: str) -> list[dict[str, object]]:
    """Gold postings first seen after the watermark, best signals first."""
    sql = f"""
        select title, company, location, url, desired_tech_hits, title_match,
               first_seen_at
        from {storage.gold_table(settings)}
        where first_seen_at > cast(? as timestamp)
        order by title_match desc, desired_tech_hits desc,
                 posted_or_updated_at desc nulls last
    """
    return storage.query_rows(sql, params=[watermark], settings=settings)


def read_warnings(settings: Settings) -> list[str]:
    """Warned source names from this run's ingest summary (absent file = none:
    the digest may run standalone, after a run whose summary wasn't kept)."""
    path = Path(settings.summary_path)
    if not path.exists():
        return []
    warnings = json.loads(path.read_text()).get("warnings", [])
    return [str(w) for w in warnings]


def build_email(
    rows: list[dict[str, object]], warnings: list[str], settings: Settings
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
        signals = f"tech hits: {r['desired_tech_hits']}"
        if r["title_match"]:
            signals += ", title match"
        text_lines.append(f"- {title} @ {company} ({location}) [{signals}]\n  {url}")
        html_items.append(
            f'<li><a href="{html.escape(url, quote=True)}">{html.escape(title)}</a>'
            f" @ {html.escape(company)} ({html.escape(location)})"
            f" <small>[{html.escape(signals)}]</small></li>"
        )

    footer_text = ""
    footer_html = ""
    if warnings:
        joined = ", ".join(warnings)
        footer_text = f"\n\nWarnings: low/zero volume from {joined}."
        footer_html = f"<p><small>Warnings: low/zero volume from {html.escape(joined)}.</small></p>"

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
    msg = build_email(rows, read_warnings(settings), settings)
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
