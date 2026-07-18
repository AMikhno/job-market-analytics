# 0019 — email digest delivery; GitHub-native failure alerting (Slack retired)

**Status:** accepted

V1 shipped with a Slack webhook for failure alerts and low-volume warnings — and in practice
it was disabled (`if: false`) with no webhook configured, so failures were silent. Rather than
resurrect a channel with nothing on the other end, delivery and alerting are redesigned around
what a single-user pipeline actually needs:

**Decision.**

1. **Failure alerting is GitHub-native.** A failed scheduled run already emails the workflow's
   actor; no webhook to configure, nothing to rot. The Slack steps (one of which — `curl -sf`
   in the *warning* path — could hard-fail the whole run on a dead webhook) are removed, along
   with the unused `slack_webhook_url` setting.
2. **Warnings annotate, never fail.** Low/zero-volume warnings surface as `::warning::`
   annotations + the step summary on the run page, and ride along as a footer in the digest.
3. **Content is delivered by email digest** (`deliver/digest.py`, `make deliver`): after each
   successful prod run, postings whose `first_seen_at` is newer than the last digest's
   **watermark** (`ops.digest_runs`) are emailed — title/company/location/link, ordered by the
   soft signals (`title_match`, then `desired_tech_hits`). **No email when nothing is new.**
   The first run bootstraps with a 26h lookback instead of dumping all of gold. The watermark
   makes delivery exactly-once-ish: a run that fails before dbt finishes simply leaves the
   watermark put, and the next successful run delivers the backlog.
4. **Transport is Gmail SMTP over SSL via stdlib `smtplib`** — unit-testable Python, no new
   dependency, and no third-party mail *action* handling credentials (the workflow's actions
   are SHA-pinned precisely because it holds `id-token: write`). `SMTP_USER` / `SMTP_PASSWORD`
   (a Gmail app password) are GitHub Actions **secrets** — real credentials, unlike the company
   list (ADR-0011). Unset credentials disable the digest, so dev/CI runs need nothing.
5. **Posting fields are untrusted web content** even in email: the HTML part escapes every
   field and never embeds `description_html`. (The same posture §5.6 of `ARCHITECTURE.md`
   takes toward V2 prompts.)

Rejected: a third-party mail action (supply-chain surface for creds); a transactional email
service (another vendor for a personal digest); sending the full active list every run (re-reads
the same postings twice a day). V2's relevance scoring will reorder/trim this digest, not
replace the channel.
