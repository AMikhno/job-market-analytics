"""Obfuscation of private identifiers for logs that end up somewhere public.

The company list is private config, but this repo is public and so are its
GitHub Actions logs. Anything logged on a failure path is therefore world
-readable, and per-company failures (a board 404s, a company moves ATS) are
routine rather than exceptional.

Scope, stated plainly: this is obfuscation for a casual reader of a public
log, not a cryptographic control. Company names come from a small, guessable
vocabulary, so an unsalted digest is reversible by anyone willing to hash a
list of candidate names. It stops a passer-by from reading the target list
out of a build log; it does not defeat someone who sets out to recover it.
The real control is that full values stay in private sinks only -- BigQuery
`ops.ingest_runs` and the gitignored run summary.
"""

from __future__ import annotations

import hashlib

_DIGEST_CHARS = 8


def redact_ref(value: str) -> str:
    """Return a short, stable stand-in for a private identifier.

    Stable across runs, so the same board is recognisable from one build log
    to the next without the value itself ever being printed.
    """
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]
    return f"redacted:{digest}"
