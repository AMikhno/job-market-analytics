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
from typing import Final

from shared import storage
from shared.config import Settings, get_settings
from shared.models import RunSummary

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("deliver")


def fetch_new_postings(settings: Settings, watermark: str) -> list[dict[str, object]]:
    """Gold postings first seen after the watermark, best fit first.

    The assessment tier leads where there is one (ADR-0031), so the email reads
    fit, near, reach, then whatever was not assessed. Within a tier
    `fit_score` leads, `match_score` (ADR-0024) breaks ties and carries the
    unscored tail. `nulls last` is what keeps the score an ordering and not a
    filter (ADR-0020): an unscored posting sinks below scored ones but still
    ships, and while nothing is scored the order is exactly the V1 order.
    """
    sql = f"""
        select title, company, location, url, match_score, desired_tech_hits,
               title_match, deal_breaker_hits, deal_breaker_terms, first_seen_at,
               fit_score, company_type, geo_restriction, manages_people,
               similarity, best_match_source,
               fit_tier, field_relation, fit_why, fit_gap
        from {storage.gold_table(settings)}
        where first_seen_at > cast(? as timestamp)
        order by tier_rank desc nulls last,
                 fit_score desc nulls last,
                 match_score desc,
                 posted_or_updated_at desc nulls last
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
    return " ".join(
        p
        for p in (
            _health(summary),
            _volume_drops(summary),
            _unconfigured(summary),
            _skipped(summary),
        )
        if p
    )


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


def _volume_drops(summary: RunSummary) -> str:
    """Sources that landed far below their own recent runs.

    The zero-rows warning catches a source that died outright. This catches the
    quieter case the census makes visible: a source still returning plenty of
    rows that has lost most of its boards.
    """
    drops = summary.volume_drops
    if not drops:
        return ""
    detail = ", ".join(f"{d.source} {d.rows} vs ~{d.baseline}" for d in drops)
    return f"Volume dropped sharply for {len(drops)} source(s): {detail}."


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


def _signal_line(row: dict[str, object]) -> str:
    """The bracketed signals on one digest line, in three groups.

        match 11 — LLM 4/5, vectors 82% — tech hits: 9, title match, B2B SaaS

    Keyword score, then the two AI rankings, then the details -- grouped
    because the three answer different questions and a flat comma list reads as
    one blur. The AI scores are labelled by *method* rather than merged: until
    human labels say which is right, seeing them disagree is the point.
    Similarity prints as a percentage so it cannot be mistaken for a second 1-5.

    Exceptions are annotated, norms are not: a geographic restriction shows only
    when it is a problem. Printing "canada ok" on every good line makes the
    label furniture, and the eye stops reading it.
    """
    groups: list[list[str]] = [[f"match {row['match_score']}"]]

    # "unscored" is stated, not omitted: an absent score otherwise reads as a
    # low one, and the two mean opposite things.
    ai: list[str] = []
    if row.get("fit_score") is not None:
        ai.append(f"LLM {row['fit_score']}/5")
    if row.get("similarity") is not None:
        ai.append(f"vectors {float(row['similarity']) * 100:.0f}%")  # type: ignore[arg-type]
    groups.append(ai or ["unscored"])

    details: list[str] = [f"tech hits: {row['desired_tech_hits']}"]
    if row.get("title_match"):
        details.append("title match")
    # Named, not just counted: "Kafka" as a nice-to-have reads very differently
    # from a posting built on Spark + Flink, and only you can tell which.
    if row.get("deal_breaker_terms"):
        details.append(f"mentions {row['deal_breaker_terms']}")
    if row.get("company_type"):
        details.append(str(row["company_type"]))
    # Attributed to the description on purpose: the location earlier in the line
    # is the board's own field, and the two disagree honestly in exactly the case
    # this exists to catch -- board says "Remote", text says US-only. Naming the
    # source makes that read as a warning rather than a bug.
    #
    # A posting open to both countries shows one location per job_key but shares
    # one content_hash, so a US-located row can legitimately carry canada_ok.
    # Counting that as a disagreement once overstated the error rate sevenfold.
    geo = row.get("geo_restriction")
    if geo == "us_only":
        details.append("text says US only")
    elif geo == "unclear":
        details.append("eligibility unstated")
    if row.get("manages_people") == "yes":
        details.append("manages people")
    # What the embedding actually matched — the thing a 1-5 cannot tell you. The
    # source, not the bullet text: enough to recognise, short enough to fit.
    if row.get("best_match_source"):
        details.append(f"like: {row['best_match_source']}")
    groups.append(details)

    return " — ".join(", ".join(g) for g in groups)


# The tiers delivery lists, in order, with the heading each gets. `unrelated` is
# deliberately absent: it is the one tier collapsed to a count (ADR-0031).
_BANDS: Final = (
    ("fit", "Fit — every must-have covered"),
    ("near", "Near — a waivable gap, or below your level"),
    ("reach", "Reach — a hard gap, or work you listed as an interest"),
)
_UNRELATED: Final = "unrelated"
# Enough titles to notice a posting that should not be here, not a second list.
_COLLAPSED_TITLES: Final = 25


def _posting_lines(row: dict[str, object]) -> tuple[str, str]:
    """One posting as a text line and an HTML list item. Every field is escaped
    in the HTML — posting metadata is scraped web content, not trusted markup."""
    title, company = str(row["title"]), str(row["company"])
    location = str(row["location"]) if row["location"] is not None else "location unknown"
    url = str(row["url"])
    signals = _signal_line(row)
    # The two sentences the tier rests on, so a wrong tier can be argued with.
    reasons = [str(row[k]) for k in ("fit_why", "fit_gap") if row.get(k)]
    text = f"- {title} @ {company} ({location}) [{signals}]\n  {url}"
    item = (
        f'<li><a href="{html.escape(url, quote=True)}">{html.escape(title)}</a>'
        f" @ {html.escape(company)} ({html.escape(location)})"
        f" <small>[{html.escape(signals)}]</small>"
    )
    if reasons:
        text += "".join(f"\n  {r}" for r in reasons)
        item += "".join(f"<br><small>{html.escape(r)}</small>" for r in reasons)
    return text, item + "</li>"


def _collapsed(rows: list[dict[str, object]]) -> str:
    """The unrelated postings as one line: a count, then titles without links.

    Collapsed, not dropped. The titles stay because the tier is a model's
    reading, and the only way to catch a posting wrongly sent here is to see
    its name. The rows themselves are untouched in the warehouse.
    """
    names = [f"{r['title']} @ {r['company']}" for r in rows[:_COLLAPSED_TITLES]]
    more = len(rows) - len(names)
    tail = f"; and {more} more" if more > 0 else ""
    return (
        f"{len(rows)} unrelated posting{'s' if len(rows) != 1 else ''} collapsed: "
        f"{'; '.join(names)}{tail}."
    )


def _render_postings(rows: list[dict[str, object]]) -> tuple[str, str]:
    """The posting list as (text, html).

    Banded by tier once anything has one. With nothing assessed the output is
    the flat list it always was, so the assessment being off, failing or still
    catching up changes nothing about delivery (ADR-0020).
    """
    if not any(r.get("fit_tier") for r in rows):
        lines = [_posting_lines(r) for r in rows]
        return "\n".join(text for text, _ in lines), f"<ul>{''.join(i for _, i in lines)}</ul>"

    sections = [
        (heading, [r for r in rows if r.get("fit_tier") == tier]) for tier, heading in _BANDS
    ]
    # Not assessed is its own section rather than folded into a tier: an absent
    # tier is not a low one, and the two mean opposite things.
    sections.append(("Not assessed yet", [r for r in rows if not r.get("fit_tier")]))
    text_parts: list[str] = []
    html_parts: list[str] = []
    for heading, members in sections:
        if not members:
            continue
        lines = [_posting_lines(r) for r in members]
        text_parts.append(f"{heading} ({len(members)})\n" + "\n".join(t for t, _ in lines))
        items = "".join(i for _, i in lines)
        html_parts.append(f"<h3>{html.escape(heading)} ({len(members)})</h3><ul>{items}</ul>")
    unrelated = [r for r in rows if r.get("fit_tier") == _UNRELATED]
    if unrelated:
        line = _collapsed(unrelated)
        text_parts.append(line)
        html_parts.append(f"<p><small>{html.escape(line)}</small></p>")
    return "\n\n".join(text_parts), "".join(html_parts)


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

    text_body, html_body = _render_postings(rows)

    status = _footer(summary)
    footer_text = f"\n\n{status}"
    footer_html = f"<p><small>{html.escape(status)}</small></p>"

    if rows:
        body_text = text_body
        body_html = html_body
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
