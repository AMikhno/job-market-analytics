"""Turn the ingest run summary into GitHub Actions annotations + step summary.

Replaces three `jq` invocations in the Ingest workflow. `jq` is a system binary
supplied by the runner image, not a Python package: it is not in `uv.lock`, so
this project can neither pin it nor install it, and it is absent on macOS --
which meant the three expressions could not be exercised anywhere but CI. Doing
this in Python puts the reporting on the same toolchain as the rest of the
pipeline and makes it testable.

Reports three things the run's exit status cannot express, each a way the
posting stream thins out while the run stays green:
  * low/zero volume from a source that ran;
  * boards skipped mid-run (already redacted in the summary);
  * registered sources that got no boards at all from the company list.

This reporter must never fail the run it reports on. It executes after ingest
has already landed rows, and a formatting problem here is not a pipeline
failure -- so every path exits 0 and problems degrade to a ::warning::, which
is visible without turning the run red. That is the same rationale the
workflow's healthchecks step already documents.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from shared.config import Settings, get_settings
from shared.models import RunSummary


@dataclass(frozen=True)
class Notice:
    """One reportable condition, in both forms the workflow needs."""

    annotation: str  # a ::warning:: workflow command, read by the Actions UI
    markdown: str  # the same thing for $GITHUB_STEP_SUMMARY


def _notice(text: str) -> Notice:
    return Notice(annotation=f"::warning::{text}", markdown=f":warning: {text}")


def notices(summary: RunSummary) -> list[Notice]:
    """Every condition worth surfacing, in order of how much it costs you.

    Nothing here is a failure -- failures are already non-zero exits from
    `make ingest` and are emailed by GitHub. These are the silent ones.
    """
    out: list[Notice] = []
    if summary.warnings:
        out.append(_notice(f"Low/zero volume from: {', '.join(summary.warnings)}"))
    if summary.skipped_refs:
        # Refs arrive redacted from the pipeline; this text lands in a public
        # Actions log, so it must stay that way. `make whois` resolves one.
        out.append(
            _notice(
                "Skipped boards (redacted, use `make whois REF=…`): "
                f"{', '.join(summary.skipped_refs)}"
            )
        )
    if summary.unconfigured:
        out.append(
            _notice(
                "Sources with no active boards (stale `COMPANIES_CSV_CONTENT`?): "
                f"{', '.join(summary.unconfigured)}"
            )
        )
    return out


def _read(settings: Settings) -> RunSummary | None:
    """The run summary, or None when there is nothing to report on."""
    path = Path(settings.summary_path)
    if not path.exists():
        return None
    return RunSummary.model_validate_json(path.read_text())


def _emit(items: list[Notice]) -> None:
    for item in items:
        print(item.annotation)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary and items:
        with Path(step_summary).open("a") as fh:
            fh.write("\n".join(item.markdown for item in items) + "\n")


def main() -> int:
    try:
        summary = _read(get_settings())
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        # Deliberate catch: this step reports on a run that has already landed
        # its rows. An unreadable or malformed summary is worth seeing, but it
        # is not a reason to fail the pipeline, so it degrades to a warning and
        # still exits 0.
        print(f"::warning::could not read the ingest run summary: {exc}")
        return 0
    if summary is None:
        print("::warning::no ingest run summary was found; nothing to report")
        return 0
    _emit(notices(summary))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
